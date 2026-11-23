// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import EChart from "../components/EChart";
import { EmptyState, NoBalances, NOT_ENOUGH_HISTORY, NotEnoughHistory, NotYetReported } from "../components/NoData";
import RequireData, { MissingDataCard } from "../components/RequireData";
import { AsOfBadge, Card, SegmentToggle, StatCard } from "../components/ui";
import { useData } from "../data/DataContext";
import { useSnapshots } from "../data/useSnapshots";
import { PRESET_LABELS } from "../filters/dateRange";
import type { Snapshot } from "../data/types";
import { monthInProgress, savingsRate } from "../lib/agg";
import { allocationRoute } from "../lib/allocationRoutes";
import { isReviewCandidate } from "../lib/answers";
import { categoryTable, type ChartTable, timeTable } from "../lib/chartTable";
import { checkLabel } from "../lib/checkLabels";
import { clock } from "../lib/clock";
import { baseGrid, CHART_GAIN, CHART_STRONG, CHART_TEXT, compactStep, moneyAxis, moneyTooltip, moneyValue, timeAxis,
  levelScale, wrapLegend } from "../lib/echarts";
import { debtBarLabel, decompose, firstMortgageBalance, mortgageGap, snapshotOnOrBefore, waterfallLabels } from "../lib/decomposition";
import { countOf, fmtChangeShare, fmtCompact, fmtCompactDelta, fmtDate, fmtDelta, fmtPct, fmtUSD0, humanizeDates } from "../lib/format";
import { footingNote, missingAssetAccounts, onOneFooting } from "../lib/comparableSnapshots";
import { assetsBy, cardAccountIds, carriedBack, changeShare, carriedBackLine, homeValue, LIQUID_MEANS, liquidity, netWorthDebt,
  monthlyNet, netWorthDebtNote, netWorthDelta, recentMonths } from "../lib/lifeSummary";
import { milestoneCrossings } from "../lib/milestones";
import { useRouteOpen } from "../lib/modules";
import { maskMoneyText } from "../lib/privacy";
import { CLASS_COLORS, DEBT_CLASSES, NEGATIVE, ownerColor, UNKNOWN_COLOR } from "../lib/tokens";
import { usePointerWords } from "../lib/pointerWords";
import { useIsMobile } from "../lib/useMediaQuery";
import { useVocab } from "../lib/useVocab";
import MonthToDate from "./home/MonthToDate";
import NextThirtyDays from "./home/NextThirtyDays";
import SavingsRateKpi from "./home/SavingsRateKpi";

/** What a † on a delta means. A delta is apples-to-oranges exactly when ONE of its two snapshots
 *  subtracts the mortgage and the other (one that pre-dates the mortgage data) does not; which one
 *  decides the direction. `viewed` is the snapshot the page shows (latest, or a ?snap= rewind). */
function daggerTitle(viewed: Snapshot): string {
  return viewed.mortgage_included
    ? "this snapshot pre-dates the mortgage data and doesn't subtract it, so its net worth is "
      + "too high and the delta understates real growth"
    : "the snapshot you are viewing pre-dates the mortgage data and doesn't subtract it while this one "
      + "does, so the viewed net worth is too high and the delta overstates real growth";
}
const needsDagger = (s: Snapshot, viewed: Snapshot) => s.mortgage_included !== viewed.mortgage_included;
/** the comparison chips Home shows before "Show all": the newest six, so the row never pushes the page's cards down */
const CHIPS_SHOWN = 6;
/** a band's or slice's colour: its asset class's, or its owner's by place in the export's owner list */
const groupColor = (groupBy: "by_class" | "by_owner", key: string, owners: readonly string[]) =>
  groupBy === "by_class" ? CLASS_COLORS[key] ?? UNKNOWN_COLOR : ownerColor(key, owners);

export default function Home() {
  const { data, missing } = useData();
  const navigate = useNavigate();
  const words = usePointerWords();
  // a slice leads to its page only while that page shows (the House slice goes to Balances while House is off)
  const routeOpen = useRouteOpen();
  const [sp, setSp] = useSearchParams();
  const { all: snaps, latest } = useSnapshots();
  // ?snap=YYYY-MM-DD rewinds the whole page to a historical snapshot; null with no snapshot at all
  const snapParam = sp.get("snap");
  const current = snaps.find((s) => s.snapshot_date === snapParam) ?? latest;
  const rewoundTo = current !== null && current !== latest ? current.snapshot_date : null;
  const rewound = rewoundTo !== null;
  const setSnap = (d: string | null) => {
    setSp((prev) => {
      const next = new URLSearchParams(prev);
      if (d && d !== latest?.snapshot_date) next.set("snap", d); else next.delete("snap");
      return next;
    }, { replace: true });
  };
  const [groupBy, setGroupBy] = useState<"by_class" | "by_owner">("by_class");
  // the comparison chips: every snapshot but the one shown, the newest first; all of them only on request
  const otherSnaps = useMemo(() => [...snaps].reverse().filter((s) => s !== current), [snaps, current]);
  const [allChips, setAllChips] = useState(false);
  const shownChips = allChips ? otherSnaps : otherSnaps.slice(0, CHIPS_SHOWN);
  const owners = data.assumptions.owners;
  const [showQuality, setShowQuality] = useState(false);
  const today = clock.today(data.assumptions.household.timezone);   // the household's day, read here and injected
  const nw = useMemo(() => netWorthDelta(snaps), [snaps]);
  const ttm = useMemo(() => {
    const months = recentMonths(data.cashflow, monthInProgress(data), 12);
    return { rate: savingsRate(months), months: months.length, net: monthlyNet(months) };
  }, [data]);
  const crossings = useMemo(() => milestoneCrossings(data).slice(-3), [data]);

  // the debt net worth subtracts (mortgage + margin; card balances only when the household counts cards in net worth): the mortgage line goes through the
  // same mortgageBalance as Accounts -> Liabilities and Planning -> House, except on a rewound snapshot, which keeps its own row
  const nwDebt = netWorthDebt(data.assumptions, current, !rewound, cardAccountIds(data.accounts));
  // the snapshot holds a row of a debt class (a card, a loan, a margin balance) or a row below zero (an overdrawn
  // account): without one it owes nothing it holds
  const holdsDebt = current !== null && current.rows.some((r) => DEBT_CLASSES.has(r.asset_class) || r.value < 0);
  // the home's value: the snapshot's House rows (null with none: no home to name)
  const home = homeValue(current);
  const mortgageAtStart = firstMortgageBalance(snaps);
  // the snapshots that pre-date the mortgage data, and that data's start month: null when every snapshot subtracts it
  const schedule = data.assumptions.mortgage?.schedule;
  const gap = useMemo(() => mortgageGap(snaps, schedule), [snaps, schedule]);

  const daily = data.networthDaily.series;
  // a line over time needs two points: two snapshots, or a daily line of more than one day beside the one snapshot
  const charted = snaps.length > 1 || (latest !== null && daily.length > 1);
  // the breakdown by class or owner (and a rewind to another snapshot) needs two snapshots: beside one, the chart draws
  // the daily line and the snapshot's total only, never each class as a lone dot
  const breakdown = snaps.length > 1;
  // the breakdown sets the newest snapshot beside month ends, so it reads it on the month ends' footing (a live margin
  // account net of its loan, as every month end holds it); the card says so when that moves its last point
  const footed = useMemo(() => {
    const classes = new Map(data.accounts.map((a) => [a.id, a.asset_class]));
    return onOneFooting(snaps, (id) => classes.get(id));
  }, [snaps, data.accounts]);
  const area = useMemo(() => {
    const hist = footed.snaps;
    const newest = hist[hist.length - 1]?.[groupBy] ?? {};
    const keys = (breakdown ? [...new Set(hist.flatMap((s) => Object.keys(s[groupBy])))] : [])
      .sort((a, b) => (newest[b] ?? 0) - (newest[a] ?? 0));
    // time axis: snapshots plot at their real dates (sparse, decades apart early →
    // close in 2026), so the dense SimpleFIN daily line can overlay the recent window
    const stacked = keys.map((k) => ({
      name: k, data: hist.map((s) => [s.snapshot_date, s[groupBy][k] ?? 0] as [string, number]),
    }));
    const totals = hist.map((s) => [s.snapshot_date, s.total] as [string, number]);
    const series: object[] = stacked.map(({ name: k, data }) => ({
      name: k, type: "line", stack: DEBT_CLASSES.has(k) ? "debt" : "assets",
      areaStyle: { opacity: 0.55 }, smooth: false, symbol: "circle", symbolSize: 5,
      lineStyle: { width: 1 },
      color: groupColor(groupBy, k, owners),
      data,
    }));
    series.push({
      name: "Net worth (snapshots)", type: "line", color: CHART_STRONG, symbol: "diamond",
      symbolSize: 7, lineStyle: { width: 2 }, z: 3,
      phoneMaxPins: 1,   // tuneForMobile: the three newest pins collide at 390px, so a phone shows the newest
      data: totals,
      markPoint: {
        symbol: "pin", symbolSize: 34, itemStyle: { color: "#8b5cf6" },
        // the label is wider than the pin's head: a purple text outline keeps the overhang readable on light cards
        label: { color: "#fff", fontSize: 9, textBorderColor: "#8b5cf6", textBorderWidth: 2,
          formatter: (p: any) => fmtCompact(p.value) },
        data: crossings.map((x) => ({ coord: [x.date, x.level], value: x.level })),
      },
    });
    // dense daily SimpleFIN-era net worth; partial-coverage days fade so the line
    // isn't read as solid history where balances weren't all observed yet
    if (daily.length > 1) {
      series.push({
        name: "Net worth (daily · bank feed)", type: "line", color: "#38bdf8",
        symbol: "circle", smooth: false, z: 4, lineStyle: { width: 1.5, type: "dashed" },
        data: daily.map((p) => ({
          value: [p.date, p.networth], name: `${(p.coverage * 100).toFixed(0)}% coverage`,
          symbolSize: p.coverage >= 0.99 ? 5 : 3,
          itemStyle: { opacity: p.coverage >= 0.99 ? 1 : 0.35 },
        })),
      });
    }
    // The view-data table lists the snapshots (the chart's own arrays above); the dense daily line is
    // hundreds of rows and stays the chart's alone, which the note says. The chart stacks a class a snapshot
    // does not carry as 0 (it has to draw something); the table does not: a class with no row in a snapshot
    // is a gap ("—"), because absent is not "owes nothing" - a snapshot that pre-dates the mortgage data
    // (`mortgage_included: false`) has no mortgage row at all.
    const listed = stacked.map(({ name: k }) => ({
      name: k, data: hist.filter((s) => k in s[groupBy]).map((s) => [s.snapshot_date, s[groupBy][k]] as [string, number]),
    }));
    const table: ChartTable = {
      ...timeTable([...listed, { name: "Net worth", data: totals }]),
      csvName: "net-worth-history.csv",
      label: "net worth over time",
      note: [
        ...(daily.length > 1 ? ["Snapshots only — the dashed daily line is drawn on the chart but not listed here."] : []),
        ...(breakdown ? [`— means the ${groupBy === "by_class" ? "class" : "owner"} has no rows in that snapshot (the chart stacks it as 0)`
          + (gap ? `; the mortgage is not booked before ${gap.sinceLabel} (†).` : ".")] : []),
      ].join(" "),
    };
    return {
      option: {
        grid: baseGrid,
        legend: wrapLegend(),
        dataZoom: [{ type: "inside" }],
        tooltip: moneyTooltip(),
        xAxis: timeAxis(),
        // the stacked bands (assets above zero, debts below) keep the zero baseline they are drawn from; the net-worth
        // lines alone are a level, which leaves zero by the one rule (levelScale)
        yAxis: { ...moneyAxis(fmtCompact), ...(breakdown ? {} : (() => {
          const values = [...totals.map(([, v]) => v), ...(daily.length > 1 ? daily.map((p) => p.networth) : [])];
          return levelScale(values, compactStep(Math.max(0, ...values.map((v) => Math.abs(v)))));
        })()) },
        series,
      },
      table,
    };
  }, [footed, groupBy, owners, daily, crossings, gap, breakdown]);

  // click a point to rewind: time-axis data is [date, value] (or {value:[date,…]}
  // for the daily line); map the clicked date to that snapshot, else the nearest prior
  const onChartClick = (p: { data?: unknown; name?: string }) => {
    const raw = p.data as number[] | { value?: unknown[] } | undefined;
    const date = Array.isArray(raw) ? raw[0]
      : (raw && Array.isArray(raw.value) ? raw.value[0] : p.name);
    if (typeof date !== "string") return;
    const exact = snaps.find((s) => s.snapshot_date === date);
    if (exact) return setSnap(exact.snapshot_date);
    const prior = [...snaps].reverse().find((s) => s.snapshot_date <= date);
    if (prior) setSnap(prior.snapshot_date);
  };

  // the donut divides the snapshot's assets (its rows above zero, by class or owner: lib/lifeSummary.ts assetsBy) by their
  // total, the base the liquid share reads too: a debt is in no slice. With no asset above 0 there is no total to divide,
  // and the card says so instead of drawing an empty ring (null)
  const donutOption = useMemo(() => {
    const entries = current === null ? [] : assetsBy(current, groupBy === "by_class" ? "asset_class" : "owner");
    const total = entries.reduce((s, e) => s + e.value, 0);
    if (total <= 0) return null;
    const share = Object.fromEntries(entries.map((e) => [e.name, (e.value / total * 100).toFixed(0)]));
    return {
      tooltip: { valueFormatter: (v: number | null | undefined) => moneyValue(v) },
      legend: { textStyle: { color: CHART_TEXT }, orient: "vertical", right: 0, top: "middle",
        formatter: (name: string) => `${name}  ${share[name] ?? "0"}%` },
      series: [{
        type: "pie", radius: ["45%", "72%"], center: ["38%", "50%"],
        label: { show: false }, labelLine: { show: false },
        data: entries.map(({ name, value }) => ({ name, value, itemStyle: { color: groupColor(groupBy, name, owners) } })),
      }],
    };
  }, [current, groupBy, owners]);
  // the snapshot's liquid / illiquid / debts split: the Liquid assets tile and its share of assets
  const liq = current === null ? null : liquidity(current);
  // the accounts the snapshot misses that would hold assets (lib/comparableSnapshots.ts missingAssetAccounts): the
  // liquid share and the donut are of the assets reported so far while there are any
  const unreportedAssets = useMemo(() => missingAssetAccounts(current, data.accounts).length, [current, data.accounts]);
  // the hand-stated values the history holds carried back to earlier month ends, each named with its first stated amount
  const carried = useMemo(() => carriedBackLine(carriedBack(snaps)), [snaps]);

  const warns = data.quality.checks.filter((c) => c.status === "warn" || c.status === "fail");
  // the sidebar badge's own rule (answers already applied), so Home, the badge and Data say one number
  const vocab = useVocab();
  const toReview = useMemo(() => data.transactions.filter((t) => isReviewCandidate(vocab, t)).length, [vocab, data.transactions]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">Home</h1>
        <div className="flex items-center gap-2">
          {rewoundTo !== null && (
            <button onClick={() => setSnap(null)}
              className="rounded-full bg-amber-900/50 px-2 py-0.5 text-[10px] text-amber-300 hover:bg-amber-900">
              viewing {fmtDate(rewoundTo)} — back to latest ✕
            </button>
          )}
          {current && <AsOfBadge date={current.snapshot_date} generatedAt={data.manifest.generated_at} />}
        </div>
      </div>

      {/* with no snapshot the three snapshot tiles read a bare "—": the line right under the row says why, once, in full */}
      <div data-testid="kpis-home" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {current === null || nwDebt === null || liq === null ? (
          <>
            <StatCard label="Net worth" value="—" />
            <StatCard label="Liquid assets" value="—" />
            <StatCard label="Debt in net worth" value="—" />
          </>
        ) : (
          <>
            <StatCard label="Net worth" value={fmtUSD0(current.total)}
              sub={[
                // the change names the snapshot it is measured from: with one snapshot there is none, never a change of 0;
                // its share only against an earlier total above 0
                !rewound && (nw ? <>{fmtDelta(nw.delta)}{nw.pct === null ? "" : ` (${fmtChangeShare(nw.pct)})`} vs {fmtDate(nw.prior.snapshot_date)}{needsDagger(nw.prior, current)
                  ? <span title={daggerTitle(current)}>†</span> : ""}</> : <>{NOT_ENOUGH_HISTORY} for a change</>),
                // the home and the mortgage only where the household has them: no House row, no home clause; no mortgage
                // account, no word on whether it is included
                home !== null && <>incl. {fmtCompact(home)} home (owner estimate)</>,
                data.assumptions.mortgage !== null && <>mortgage {current.mortgage_included ? "included" : "NOT included"}</>,
              ].filter(Boolean).map((part, i) => <span key={i}>{i > 0 && " · "}{part}</span>)} />
            {/* the share is of the snapshot's assets (never of net worth, so never past 100%); with no asset above zero
                there is none. While the snapshot misses an account that would hold assets, the share is of the assets
                reported so far and says so (an account of a debt kind is in no share of assets) */}
            <StatCard label="Liquid assets" value={fmtUSD0(liq.liquid)}
              sub={<>{liq.liquidShare === null ? ""
                : unreportedAssets > 0 ? <>{fmtPct(liq.liquidShare)} <NotYetReported count={unreportedAssets} of="assets" /> · </>
                : `${fmtPct(liq.liquidShare)} of assets — `}{LIQUID_MEANS}</>} />
            {/* a snapshot with no row of a debt class owes nothing it holds: "—", and no red without a debt */}
            <StatCard label="Debt in net worth" value={nwDebt.owed === 0 && !holdsDebt ? "—" : fmtUSD0(-nwDebt.owed)}
              tone={nwDebt.owed > 0 ? "neg" : undefined} href="#/accounts/liabilities"
              sub={netWorthDebtNote(nwDebt, data.assumptions.net_worth.include_credit_cards, cardAccountIds(data.accounts).size > 0)} />
          </>
        )}
        {/* the savings rate is cash flow, not a snapshot: it stays on the latest full months while the
            row above is rewound, and says so rather than silently mixing dates */}
        <RequireData compact files={["cashflow_monthly.json"]} what="The savings rate">
          <SavingsRateKpi rate={ttm.rate} months={ttm.months} net={ttm.net} rewoundTo={rewoundTo === null ? undefined : fmtDate(rewoundTo)} />
        </RequireData>
      </div>

      {/* the shown snapshot's net worth against every other snapshot: under a heading that says so, the newest first, the
          chips wrapping onto rows (every chip whole); the newest CHIPS_SHOWN, the rest behind one control at every width */}
      {/* with no snapshot this is the page's one full line about it, under the tiles it explains; the cards below say it
          short */}
      {current === null ? (
        <div data-testid="card-snapshot-deltas"><NoBalances /></div>
      ) : snaps.length < 2 ? (
        <div data-testid="card-snapshot-deltas">
          <NotEnoughHistory>the change against each earlier snapshot appears here once a second snapshot exists.</NotEnoughHistory>
        </div>
      ) : (
        <section data-testid="card-snapshot-deltas" aria-labelledby="snapshot-deltas-heading">
          <h2 id="snapshot-deltas-heading" className="mb-1.5 text-xs font-medium text-zinc-400">
            {rewoundTo === null ? "Net worth now against each earlier snapshot, newest first"
              : `Net worth on ${fmtDate(rewoundTo)} against each other snapshot, newest first`}
          </h2>
          <ul id="snapshot-deltas-list" className="flex flex-wrap gap-2">
            {shownChips.map((s) => {
              const d = current.total - s.total;
              const share = changeShare(d, s.total);
              const dagger = needsDagger(s, current);
              return (
                <li key={s.snapshot_date}
                  className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-xs"
                  title={dagger ? daggerTitle(current) : undefined}>
                  <span className="text-zinc-400">vs {fmtDate(s.snapshot_date)}{dagger ? "†" : ""}: </span>
                  {/* the share only against an earlier total above 0 (changeShare) */}
                  <span className={d >= 0 ? "text-emerald-400" : "text-red-400"}>
                    {fmtDelta(d)}{share === null ? "" : ` (${fmtChangeShare(share)})`}
                  </span>
                </li>
              );
            })}
          </ul>
          {otherSnaps.length > CHIPS_SHOWN && (
            <button type="button" aria-expanded={allChips} aria-controls="snapshot-deltas-list"
              onClick={() => setAllChips(!allChips)}
              className="mt-1.5 inline-flex min-h-[40px] items-center text-xs text-blue-400 hover:underline md:min-h-0">
              {allChips ? "Show fewer" : `Show all ${otherSnaps.length}`}
            </button>
          )}
        </section>
      )}
      {/* the footnote explains a † on screen: on a chip shown (one folded behind "Show all" is not), or on the net-worth
          tile's change */}
      {current !== null && snaps.length >= 2 && (shownChips.some((s) => needsDagger(s, current))
        || (!rewound && nw !== null && needsDagger(nw.prior, current))) && (
        <p data-testid="dagger-footnote" className="text-[10px] text-faint">
          {current.mortgage_included ? (
            <>
              † snapshots before {gap?.sinceLabel ?? "the mortgage data"} pre-date the mortgage data and don't subtract the
              mortgage balance{mortgageAtStart === null ? "" : ` (~${fmtCompact(mortgageAtStart)} at the first snapshot that includes it)`}
              {" "}— real growth since those snapshots is better than the deltas show.
            </>
          ) : (
            <>
              † the snapshot you are viewing pre-dates the mortgage data ({gap?.sinceLabel ?? "its start"}) and doesn't subtract
              the mortgage balance{mortgageAtStart === null ? "" : ` (~${fmtCompact(mortgageAtStart)} at the first snapshot that includes it)`},
              while the marked snapshots do — the deltas against them overstate real growth.
            </>
          )}
        </p>
      )}

      {/* spending so far this calendar month against the typical pace by the same day; it reads the transactions, not a
          snapshot, so it stays on the newest data while the rows above are rewound */}
      <MonthToDate />

      <RequireData files={["cashflow_monthly.json"]} what="The net-worth decomposition">
        <DecompositionCard />
      </RequireData>

      {/* no snapshot: the short line (the line under the tiles says it in full); one snapshot and no daily line: one
          point, no line over time to draw */}
      {latest === null ? (
        <Card testId="card-networth-history" title="Net worth over time">
          <NoBalances short />
        </Card>
      ) : (
      <Card testId="card-networth-history"
        title={charted && breakdown ? `Net worth over time — ${words.click} a point to rewind the page to that snapshot` : "Net worth over time"}
        // the toggle drives the allocation donut too, which one snapshot is enough for: it stays whenever there is one,
        // and with fewer than two the chart here draws the same line and total on either side
        right={(
          <SegmentToggle label="Group by" value={groupBy} onChange={setGroupBy}
            options={[
              { value: "by_class", label: "by class" },
              { value: "by_owner", label: "by owner" },
            ]} />
        )}>
        {charted
          ? <EChart option={area.option} tableData={area.table} height={380} onClick={breakdown ? onChartClick : undefined} />
          : <NotEnoughHistory>the chart draws net worth over time once a second snapshot exists.</NotEnoughHistory>}
        {charted && !breakdown && (
          <NotEnoughHistory>the breakdown by class and by owner appears once a second snapshot exists.</NotEnoughHistory>
        )}
        {missing.has("networth_daily.json") && (
          <MissingDataCard compact files={["networth_daily.json"]} what="The daily net-worth line" />
        )}
        {daily.length > 1 && (
          <p className="mt-1 text-[10px] text-faint">
            The dashed blue line is the dense daily net worth from the bank feed's balances
            (vs the sparse snapshots). Faded points are partial-coverage days —
            not every account reported a balance yet — so they read as provisional, not
            history; the line meets the latest snapshot exactly. It fills in with each
            pull of the bank feed.
          </p>
        )}
        <details className="mt-2 text-xs">
          {/* one snapshot is a date, not a span "since" a year */}
          <summary className="cursor-pointer select-none text-zinc-400">
            {snaps.length === 1 ? `1 snapshot, ${fmtDate(snaps[0].snapshot_date)}`
              : `${countOf(snaps.length, "snapshot")} since ${snaps[0].snapshot_date.slice(0, 4)}`} — show the table
          </summary>
          <table className="mt-1 w-full text-xs">
            <thead>
              <tr className="border-b border-zinc-700 text-left text-zinc-500">
                <th className="py-1">Snapshot</th><th className="text-right">Net worth</th>
              </tr>
            </thead>
            <tbody>
              {[...snaps].reverse().map((s) => (
                <tr key={s.snapshot_date} className="border-b border-zinc-800/50">
                  <td className="py-1 text-zinc-400">{fmtDate(s.snapshot_date)}{s.mortgage_included ? "" : "†"}</td>
                  <td className="text-right tabular-nums">{fmtUSD0(s.total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
        {/* a month end that holds a hand-stated value carried back (before its first value an asset is held at it) says
            which asset, at which first stated amount and since when */}
        {carried !== null && (
          <p data-testid="networth-history-carried" className="mt-1 text-[10px] text-faint">{carried}</p>
        )}
        {footed.folded && breakdown && (
          <p data-testid="networth-history-footing" className="mt-1 text-[10px] text-faint">{footingNote("above")}</p>
        )}
        {/* the pins' line only where the chart draws a pin */}
        {charted && breakdown && (crossings.length > 0 || gap) && (
          <p data-testid="networth-history-notes" className="mt-1 text-[10px] text-faint">
            {[
              crossings.length > 0 && <>pins mark the latest interpolated $100K milestone crossings (the newest alone on a phone)</>,
              gap && <>† snapshots before {gap.sinceLabel} don't subtract the mortgage — the dip where it enters is liability
                recognition, not lost wealth</>,
            ].filter(Boolean).map((part, i) => <span key={i}>{i > 0 && " · "}{part}</span>)}
          </p>
        )}
      </Card>
      )}

      {/* grid-cols-1 = minmax(0,1fr): the list's nowrap rows would otherwise stretch the phone column */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {current === null ? (
          <Card testId="card-allocation-donut" title="Current allocation of assets"><NoBalances short /></Card>
        ) : donutOption === null ? (
          <Card testId="card-allocation-donut" title="Current allocation of assets">
            {/* what is true of the snapshot shown (a rewound view names its date) */}
            <EmptyState>
              Nothing to divide yet: no asset above zero in {rewoundTo === null ? "the newest snapshot"
                : `the snapshot of ${fmtDate(rewoundTo)}`}.
            </EmptyState>
          </Card>
        ) : (
          <Card testId="card-allocation-donut" title={`Current allocation of assets — ${words.click} a slice for detail`}>
            <EChart option={donutOption} height={300}
              onClick={(p) => {
                const to = allocationRoute(String(p.name), routeOpen);
                if (to) navigate(to);
              }} />
            {/* the slices divide the assets reported so far: while the snapshot misses an account that would hold
                assets, the card says so */}
            {unreportedAssets > 0 && (
              <p className="mt-1 text-xs text-zinc-400">Shares <NotYetReported count={unreportedAssets} of="assets" /></p>
            )}
          </Card>
        )}
        <NextThirtyDays today={today} />
      </div>

      <RequireData files={["quality_report.json", "open_questions.json"]} what="The data-quality panel">
        <Card title={`Data quality — ${countOf(warns.length, "warning")} · ${countOf(data.questions.length, "open question")} · ${toReview} to review`}
          testId="card-data-quality"
          right={
            <button onClick={() => setShowQuality(!showQuality)} aria-expanded={showQuality}
              className="inline-flex min-h-[40px] min-w-[40px] items-center justify-center text-xs text-blue-400 underline md:min-h-0 md:min-w-0">
              {showQuality ? "hide" : "show"}
            </button>
          }>
          {showQuality && (
            <div className="space-y-3 text-xs">
              {/* a check name repeats when the ETL runs a check once per statement file, so a row's key is its place too */}
              {warns.map((c, i) => (
                <div key={`${i}:${c.name}`} className="rounded border border-amber-900/60 bg-amber-950/30 p-2">
                  <span title={c.name} className="font-medium text-amber-400">{checkLabel(c.name, data.accounts, data.manifest.setup.feed)}</span>
                  <p className="text-zinc-400">{humanizeDates(maskMoneyText(c.detail))}</p>
                </div>
              ))}
              <p className="text-zinc-500">
                Open questions, data gaps, the uncategorized pile and the learned rules are on{" "}
                <Link to="/data" className="text-blue-400 hover:underline">Data</Link>.
              </p>
            </div>
          )}
        </Card>
      </RequireData>
    </div>
  );
}

/** Where the net-worth change came from: savings vs markets vs home vs debt, as a waterfall between two snapshots.
 *  Honesty rules live in lib/decomposition.ts (like-with-like debt, mortgage-boundary, coverage). */
function DecompositionCard() {
  const { data, prefs } = useData();
  const { all: snaps, latest, prior } = useSnapshots();
  const [period, setPeriod] = useState<"12m" | "ytd" | "all">("12m");
  const phone = useIsMobile();
  const gap = mortgageGap(snaps, data.assumptions.mortgage?.schedule);

  const d = useMemo(() => {
    if (latest === null || prior === null) return null;
    const end = latest.snapshot_date;
    const target = period === "all" ? snaps[0].snapshot_date
      : period === "ytd" ? `${end.slice(0, 4)}-01-01`
      : `${Number(end.slice(0, 4)) - 1}${end.slice(4)}`;
    const from = period === "all" ? snaps[0] : snapshotOnOrBefore(snaps, target);
    if (!from || from === latest) return null;
    return decompose(from, latest, data.cashflow, data.assumptions,
                     prefs.doc.payroll.phases);
  }, [snaps, latest, prior, period, data.cashflow, data.assumptions, prefs.doc.payroll.phases]);

  const chart = useMemo(() => {
    if (!d) return null;
    const deltas: { label: string; value: number }[] = [
      { label: "Cash savings", value: d.cashSavings },
      ...(d.retirement !== 0 ? [{ label: "Retirement", value: d.retirement }] : []),
      ...(d.home !== 0 ? [{ label: "Home", value: d.home }] : []),
      // named by its sign, so no reader has to decode a negative "paydown"
      { label: debtBarLabel(d.debt, d.mortgageExcluded), value: d.debt },
      { label: "Market & other", value: d.market },
    ];
    // the tooltip names each column in full; the axis wraps them short on a phone (every one named)
    const labels = waterfallLabels(deltas.map((x) => x.label), d.from.snapshot_date, d.to.snapshot_date, false);
    const axisNames = waterfallLabels(deltas.map((x) => x.label), d.from.snapshot_date, d.to.snapshot_date, phone);
    const offsets: number[] = [0];
    const bars: number[] = [d.from.total];
    const colors: string[] = ["#3b82f6"];
    let running = d.from.total;
    for (const x of deltas) {
      if (x.value >= 0) {
        offsets.push(running);
        bars.push(x.value);
        colors.push(CHART_GAIN);
        running += x.value;
      } else {
        running += x.value;
        offsets.push(running);
        bars.push(-x.value);
        colors.push(NEGATIVE);
      }
    }
    offsets.push(0);
    bars.push(d.to.total);
    colors.push("#3b82f6");
    const signed = [d.from.total, ...deltas.map((x) => x.value), d.to.total];
    // the table lists the same `signed` values the tooltip and the bar labels read, under the full column names
    // "Amount", not "Change": the Start and Now rows are net-worth levels, the rows between are changes
    const table: ChartTable = { ...categoryTable("Component", labels, [{ name: "Amount", data: signed }]),
      csvName: "net-worth-decomposition.csv", label: "where the net-worth change came from" };
    const option = {
      grid: baseGrid,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (ps: { dataIndex: number }[]) => {
          const i = ps[0].dataIndex;
          const v = signed[i];
          const isEdge = i === 0 || i === labels.length - 1;
          return `${labels[i]}: <b>${isEdge ? fmtUSD0(v) : fmtDelta(v)}</b>`;
        },
      },
      // a phone keeps every label (hideOverlap off, tuneForMobile honours it): they are sized to fit
      xAxis: { type: "category", data: axisNames,
        axisLabel: { color: CHART_TEXT, interval: 0, fontSize: phone ? 9 : 10, lineHeight: 11, hideOverlap: !phone } },
      yAxis: moneyAxis(fmtCompact),
      series: [
        { name: "offset", type: "bar", stack: "wf", silent: true,
          itemStyle: { color: "transparent" }, data: offsets, tooltip: { show: false } },
        { name: "change", type: "bar", stack: "wf",
          data: bars.map((v, i) => ({ value: v, itemStyle: { color: colors[i] } })),
          label: { show: true, position: "top", color: CHART_TEXT, fontSize: 10,
            formatter: (p: { dataIndex: number }) => {
              const v = signed[p.dataIndex];
              const isEdge = p.dataIndex === 0 || p.dataIndex === labels.length - 1;
              return isEdge ? fmtCompact(v) : fmtCompactDelta(v);
            } } },
      ],
    };
    return { option, table };
  }, [d, phone]);

  const title = "Where the change came from — saving vs markets";
  const toggle = (
    <SegmentToggle label="Period" value={period} onChange={setPeriod}
      options={[
        // the shared preset words (filters/dateRange PRESET_LABELS): 12M, YTD, All - not "1y" and "all"
        { value: "12m", label: PRESET_LABELS["12m"] },
        { value: "ytd", label: PRESET_LABELS.ytd },
        { value: "all", label: PRESET_LABELS.all },
      ]} />
  );
  // nothing to compare (no snapshot, or one so far): the card stays and says why, instead of vanishing; with two or more
  // snapshots the toggle stays, so a window with no earlier snapshot can be widened. With no snapshot it says so short:
  // the line under the page's tiles carries the long one
  if (latest === null) return <Card testId="card-decomposition" title={title}><NoBalances short /></Card>;
  if (prior === null) {
    return (
      <Card testId="card-decomposition" title={title}>
        <NotEnoughHistory>the waterfall splits the change between two snapshots and appears once a second snapshot exists.</NotEnoughHistory>
      </Card>
    );
  }
  if (!d || !chart) {
    return (
      <Card testId="card-decomposition" title={title} right={toggle}>
        <p data-testid="decomposition-empty" className="text-xs text-zinc-500">
          No earlier snapshot in this window to compare with — pick a longer one.
        </p>
      </Card>
    );
  }
  return (
    <Card testId="card-decomposition" title={title} right={toggle}>
      {d.coverageIncomplete && (
        <p className="mb-1 rounded border border-amber-900/60 bg-amber-950/30 px-2 py-1 text-[10px] text-amber-300">
          Cash-flow data is incomplete in this window — savings are understated and the
          "market & other" residual overstated.
        </p>
      )}
      <EChart option={chart.option} tableData={chart.table} height={280} />
      <p className="mt-1 text-[10px] text-faint">
        Components sum to the change by construction; "market & other" is the residual
        (market moves, valuation changes, gifts, anything cash-flow data missed).
        {d.mortgageExcluded && (
          <> *The debt bar uses the change in the loan account's balance inside the window — this window crosses
          {" "}{gap?.sinceLabel ?? "the start of the mortgage data"}, before which the mortgage isn't booked, so the raw
          class Δ would be an accounting artifact.</>
        )}
        {d.marginFolded && (
          <> A margin loan is counted inside its brokerage account at both ends, as month ends hold it, so the debt bar
          compares the same debts; the loan's change is in "market & other".</>
        )}
        {d.retirement === 0 && (
          <> Enter payroll facts (
          <a href="#/cash-flow/budget" className="text-blue-400 underline">Cash Flow → Budget</a>
          ) to split out 401(k) contributions.</>
        )}
      </p>
    </Card>
  );
}

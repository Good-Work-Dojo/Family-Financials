// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo, useState } from "react";
import AssumptionsPanel, { AssumptionField } from "../components/AssumptionsPanel";
import DefaultTag from "../components/DefaultTag";
import EChart from "../components/EChart";
import GatedLink, { WhileShown } from "../components/GatedLink";
import { Code, EmptyState, historySoFar, NoBalances, NoTransactions, NO_TRANSACTIONS_YET, NotEnoughHistoryYet,
  RUN_RATE_NEEDS, runRateWaits } from "../components/NoData";
import SplitNote from "../components/SplitNote";
import RequireData from "../components/RequireData";
import { Card, PHONE_WRAP, SegmentToggle, StatCard } from "../components/ui";
import { useData } from "../data/DataContext";
import { useSnapshots } from "../data/useSnapshots";
import type { AppData, Household, Snapshot } from "../data/types";
import {
  currentYear, dataFirstDay, monthInProgress, nextFullMonthEnds, RUN_RATE_MIN_MONTHS, runRateMonths, runRateWindow,
} from "../lib/agg";
import { categoryTable } from "../lib/chartTable";
import {
  baseGrid, baseLegend, CHART_STRONG, CHART_SURFACE, CHART_TEXT, gaugeReading, moneyAxis, moneyTooltip, monthAxis,
} from "../lib/echarts";
import {
  type CoreSpend, emergencyCoverage, emergencyWindow, overrideFor, type SpendRow, toggleEssential, trailingYearCoreSpend,
} from "../lib/emergency";
import { useEfundOverrides, writeEfundOverrides } from "../lib/efundOverrides";
import { type CashForecast, FORECAST_MIN_MONTHS, forecastCashflow } from "../lib/forecast";
import { holdsShockable, SHOCK_DEFAULTS, type Shocks, shockedTotal } from "../lib/forwardLook";
import { countOf, fmtCompact, fmtDate, fmtDelta, fmtMonth, fmtMonths, fmtPct, fmtUSD0 } from "../lib/format";
import { liquidCash as liquidCashOf, savingsPace } from "../lib/lifeSummary";
import { isProjectionDefault } from "../lib/projectionDefaults";
import { fireCrossing, fireNumber, projectGrowth } from "../lib/projections";
import { retirementBalances } from "../lib/retirement";
import { SCROLL_TARGETS, useScrollToCardParam } from "../lib/scrollTargets";
import { HOUSE_CLASS, RETIREMENT_CLASS } from "../lib/tokens";
import { useGrowthSeed, useTouched } from "../lib/useGrowthSeed";
import { useVocab } from "../lib/useVocab";
import ForwardLookStrip from "./planning/ForwardLookStrip";

/** Why a planning run-rate is unknown: no transaction at all (each card says so), or fewer full months than agg.ts
 *  RUN_RATE_MIN_MONTHS (`have`: how many; the page's one line, PlanningHistory, says what is needed and how many there
 *  are, and the cards and tiles say only the short form). */
interface RunRateGap { noTransactions: boolean; have: number }

/** the household's RunRateGap (whether a run-rate is known is the card's own question: runRateWindow) */
function runRateGap(data: AppData): RunRateGap {
  return { noTransactions: data.transactions.length === 0,
    have: runRateMonths(data.cashflow, monthInProgress(data), dataFirstDay(data.transactions)).length };
}

/** a KPI tile's sub-line for a run-rate it lacks: the no-transactions line, or, with too few full months, what it needs
 *  and how many there are (runRateWaits) */
const gapShort = (gap: RunRateGap): string | undefined => (gap.noTransactions ? NO_TRANSACTIONS_YET
  : gap.have < RUN_RATE_MIN_MONTHS ? runRateWaits(gap.have) : undefined);

/** a card's line for a run-rate it lacks: the no-transactions line, or the short not-enough-history line (the page's
 *  line says what is needed and how many full months there are) */
function GapLine({ gap }: { gap: RunRateGap }) {
  return gap.noTransactions ? <NoTransactions /> : <NotEnoughHistoryYet />;
}

/** "from 5 full months" beside a figure a run-rate over fewer than twelve months gives; nothing over twelve */
const fromMonths = (months: number): string | null => (months < 12 ? `from ${countOf(months, "full month")}` : null);

/** the savings pace a page reads (lib/lifeSummary.ts savingsPace): `pace` the real monthly figure, `seed` what a
 *  savings slider starts at (the pace rounded to its $250 steps, never below none), `months` the full months behind it */
interface PaceSeed { pace: number; seed: number; months: number }

/** the reason a known pace gives no savings to plan from, after the word "income": it did not cover spending over the
 *  pace's months, by how much a month */
const noSurplus = (p: PaceSeed): string =>
  `did not cover spending over the last ${countOf(p.months, "full month")} (${fmtUSD0(p.pace)}/mo)`;

/** The one line Projections says while its run-rates or its forecast wait for full months: what the page plans from,
 *  what each part needs, how many full months there are and when the next one ends (the month in progress, from the
 *  exports). The run-rates and the forecast count the same months (lib/agg.ts runRateMonths), so the forecast draws
 *  exactly when the line would say six. `nextEnds` is the day the next full month ends (lib/agg.ts nextFullMonthEnds:
 *  null when the data stops short of the ingest month). Nothing when every part has what it needs, or when there is no
 *  transaction at all (each card says so). */
function PlanningHistory({ gap, nextEnds }: { gap: RunRateGap; nextEnds: string | null }) {
  if (gap.noTransactions) return null;
  if (gap.have >= Math.max(RUN_RATE_MIN_MONTHS, FORECAST_MIN_MONTHS)) return null;
  return (
    <p data-testid="planning-history" className="rounded-xl border border-zinc-800 bg-zinc-900/40 px-4 py-3 text-sm text-zinc-300">
      Projections plans from the household's own spending and saving: the savings pace, the FIRE number and the emergency
      fund need {RUN_RATE_NEEDS}, the cash-flow forecast {countOf(FORECAST_MIN_MONTHS, "full month")}.{" "}
      {historySoFar(gap.have, nextEnds)}
    </p>
  );
}

export default function Tools() {
  const { data, missing } = useData();
  useScrollToCardParam();   // a `?card=emergency-fund` link scrolls to its card
  // the newest snapshot; null for a household with none yet, whose snapshot cards say so
  const cur = useSnapshots().latest;

  const liquidCash = useMemo(() => liquidCashOf(cur), [cur]);
  // the device's essential toggles (the gauge below edits them) feed the forecast's safety floor too; null with too
  // little history to average (no floor, never one of $0)
  const efund = useEfundOverrides();
  const vocab = useVocab();
  // the month in progress (lib/agg.ts monthInProgress): every trailing figure below reads the full months before it, and
  // each planning run-rate needs RUN_RATE_MIN_MONTHS of them (lib/agg.ts runRateWindow)
  const inProgress = useMemo(() => monthInProgress(data), [data]);
  // the day the data begins: a first month it covers in part is no full month for a run-rate (lib/agg.ts runRateMonths)
  const firstDay = dataFirstDay(data.transactions);
  const essentials = useMemo(
    () => emergencyCoverage(vocab, data.transactions, emergencyWindow(data.cashflow, inProgress, firstDay), liquidCash,
      efund).essential,
    [vocab, data.transactions, data.cashflow, inProgress, firstDay, liquidCash, efund]);

  // a year of CORE spending (the twelve full months, or fewer annualised): excludes one-off checks/wires/ATM cash
  // (money movements, not recurring living costs) so FIRE and emergency-fund
  // math isn't inflated by single events like a $15K check; null with too little history
  const coreSpend12 = useMemo(
    () => trailingYearCoreSpend(vocab, data.transactions, runRateWindow(data.cashflow, inProgress, 12, firstDay)),
    [vocab, data.transactions, data.cashflow, inProgress, firstDay]);

  const hh = data.assumptions.household;
  const year = currentYear(data.manifest);
  // retirement accounts -> person, via assumptions.household.retirement_accounts (account name -> person):
  // holdings.json plus any retirement account the feed reports only a balance for, each counted once; null with no
  // snapshot
  const owners = useMemo(() => hh.people.filter((p) => p.role === "owner").map((p) => p.name), [hh.people]);
  const retirement = useMemo(() => retirementBalances(data.holdings, cur, hh.retirement_accounts, owners),
    [data.holdings, cur, hh.retirement_accounts, owners]);

  // the savings pace (lib/lifeSummary.ts savingsPace: the twelve full months before the month in progress, or as many as
  // there are) and the slider's seed from it: the pace rounded to the slider's step, never below none. With too little
  // history there is no pace: null, and NetWorthCard and MarketShockCard say so instead of labelling a stand-in "your
  // actual"; a pace at or below none is named as what it is, never as a seed of $0 called "your actual" (both cards are
  // also gated on cashflow_monthly.json below)
  const avgMonthlySavings = useMemo((): PaceSeed | null => {
    const pace = savingsPace(data.cashflow, inProgress, firstDay);
    return pace === null ? null
      : { pace: pace.monthly, seed: Math.max(0, Math.round(pace.monthly / 250) * 250), months: pace.months };
  }, [data.cashflow, inProgress, firstDay]);
  const gap = useMemo(() => runRateGap(data), [data]);
  // the spending-cut card asks what a cut of the household's spending grows to: with too little history to know what it
  // spends, its slider starts at none and its tiles wait for a cut the person sets
  const spendingKnown = useMemo(() => runRateWindow(data.cashflow, inProgress, 12, firstDay) !== null,
    [data.cashflow, inProgress, firstDay]);

  // With no snapshot the first card that needs one says so in full and the cards after it in short: the forecast, unless
  // its file is missing; then the retirement projection, unless its file is missing too; then the emergency fund. The
  // net-worth projection and the stress test read the forecast's file, so they come after it whenever they are shown.
  const fullLineAt = !missing.has("cashflow_monthly.json") ? "forecast" : !missing.has("holdings.json") ? "retirement" : "emergency";

  return (
    <div className="space-y-4">
      <PlanningHistory gap={gap} nextEnds={nextFullMonthEnds(data)} />
      <ForwardLookStrip />
      <RequireData files={["cashflow_monthly.json"]} what="The cash-flow forecast">
        <ForecastCard liquidCash={liquidCash} floor={essentials === null ? null : essentials * 3}
          asOf={cur === null ? null : cur.snapshot_date} />
      </RequireData>
      <RequireData files={["holdings.json"]} what="The retirement projection">
        {retirement === null
          ? <Card testId="card-retirement" title="Retirement projection"><NoBalances short={fullLineAt !== "retirement"} /></Card>
          : <RetirementCard hh={hh} year={year} retirementBy={retirement.byPerson} reported={retirement.reported}
              reportedAccounts={retirement.reportedAccounts}
              retirementAsOf={retirement.asOf} balanceOnly={retirement.balanceOnly} coreSpend={coreSpend12} />}
      </RequireData>
      {/* two unlike cards side by side: each is as tall as its own content (stretched to its neighbour, the gauge card
          was a tall empty box) */}
      <div className="grid items-start gap-4 lg:grid-cols-2">
        <RequireData files={["cashflow_monthly.json"]} what="The net-worth projection">
          {cur === null
            ? <Card testId="card-networth-projection" title="Net worth projection"><NoBalances short /></Card>
            : <NetWorthCard total={cur.total} house={holdsHouse(cur)} prefill={avgMonthlySavings} gap={gap} year={year} />}
        </RequireData>
        {/* liquid only: an escrow balance is Cash-class but unreachable */}
        <EmergencyFund liquidCash={liquidCash} shortNoBalances={fullLineAt !== "emergency"} />
      </div>
      <RequireData files={["cashflow_monthly.json"]} what="The market-shock stress test">
        <MarketShockCard avgMonthlySavings={avgMonthlySavings} />
      </RequireData>
      <SpendingCutCard total={cur === null ? null : cur.total} spendingKnown={spendingKnown} gap={gap} />
      <p className="text-[10px] text-faint">
        {/* only for a household that has the House page: without a mortgage there is no what-if to point at */}
        <WhileShown to="/planning/home">
          The mortgage extra-payment what-if lives in{" "}
          <GatedLink to="/planning/home" className="text-blue-400 hover:underline">Planning → House</GatedLink>.{" "}
        </WhileShown>
        All projections are
        deterministic illustrations with the stated assumptions — not forecasts. A slider or a figure tagged
        “default” rests on a starting value nobody in the household stated. The withdrawal
        rate the retirement sliders start from is edited on{" "}
        <a href="#/data" className="text-blue-400 hover:underline">Data</a>.
      </p>
    </div>
  );
}

const FORECAST_TITLE = "Cash flow forecast — next 12 months (estimate)";

/** The forecast chart's caption: what each thing it draws is - the line, the band only when drawn (`band`), the floor
 *  only when drawn (`floor`) - and where the forecast starts: the rest of the month the starting balance is dated in,
 *  or the month after one it covers to its last day (`asOf`, the balance's date). */
export function forecastCaption(f: CashForecast, band: boolean, floor: boolean, asOf: string | null): string {
  const drawn = ["The line is projected liquid cash",
    ...(band ? ["the shaded band ±1σ of monthly net, widening with √months"] : []),
    ...(floor ? ["the dashed line three months of essential spending"] : [])];
  const said = drawn.length === 1 ? drawn[0] : `${drawn.slice(0, -1).join(", ")} and ${drawn[drawn.length - 1]}`;
  const p = f.partialFirst;
  const start = p !== null
    ? ` Today's cash is the balance of ${fmtDate(p.asOf)}, which already holds every day up to it, so the first point is`
      + ` only the rest of ${fmtMonth(p.month)}, the month in progress (${countOf(p.days, "day")}).`
    : asOf !== null && f.months[0].month > asOf.slice(0, 7)
      ? ` Today's cash is the balance of ${fmtDate(asOf)}, the last day of its month, so the forecast starts with`
        + ` ${fmtMonth(f.months[0].month)}.`
      : "";
  return `${said}.${start} Income and spend baselines come from the household's own cash flow. An estimate for`
    + " planning — big one-offs (tax bills, home projects) aren't predicted.";
}

/** 12-month liquid-cash forecast: trailing baselines + month-of-year seasonality (exported for its test). `liquidCash` is
 *  null with no snapshot and `floor` (three months of essentials) with no transaction to average: the card then says
 *  what is missing, and draws no floor it does not have. Today's cash is the newest snapshot's (dated `asOf`), which
 *  already holds every day up to that date, so the first month is only the rest of the month it is dated in
 *  (lib/forecast.ts `forecastStart`), and the caption says so; the caption names only what the chart draws. */
export function ForecastCard({ liquidCash, floor, asOf }: { liquidCash: number | null; floor: number | null; asOf: string | null }) {
  const { data } = useData();
  // a floor of 0 is "nothing is essential" (every row toggled off in the gauge below), not "a floor of
  // $0 that is always met": the check reads "—" and the chart draws no line, like the runway KPI does
  const shownFloor = floor !== null && floor > 0 ? floor : null;
  const hasFloor = shownFloor !== null;
  // the history is the full months before the month in progress (lib/agg.ts monthInProgress), less a first month the
  // data covers only in part (runRateMonths: the months the page's line counts)
  const inProgress = useMemo(() => monthInProgress(data), [data]);
  const f = useMemo(
    () => (liquidCash === null ? null : forecastCashflow({
      cashflow: data.cashflow, inProgress, firstDay: dataFirstDay(data.transactions), startCash: liquidCash, asOf,
      floor: shownFloor ?? undefined,
      schedules: data.recurring,
      transactions: data.purchases,   // one row per charge: a subscription is one series (equal to the parts' sum, lib/forecast.test.ts)
    })),
    [data.cashflow, data.transactions, inProgress, data.recurring, data.purchases, liquidCash, shownFloor, asOf]);
  // the band is drawn only when it has a width: a net that never varied gives none, and the caption then names none
  const band = f !== null && f.netStdev > 0;
  const option = useMemo(() => {
    if (!f) return {};
    return {
      grid: baseGrid,
      tooltip: {
        trigger: "axis",
        formatter: (ps: any[]) => {
          const m = f.months[ps[0].dataIndex];
          const rest = ps[0].dataIndex === 0 && f.partialFirst !== null ? ` — the rest of the month, ${countOf(m.days, "day")}` : "";
          return `<b>${fmtMonth(m.month)}${rest}</b>`
            + `<div>projected cash: <b>${fmtUSD0(m.cumCash)}</b>${band ? ` (${fmtCompact(m.low)}–${fmtCompact(m.high)})` : ""}</div>`
            + `<div style="color:#a1a1aa">income ~${fmtUSD0(m.income)} · spend ~${fmtUSD0(m.spend)} · scheduled ~${fmtUSD0(m.scheduled)} · net ${fmtDelta(m.net)}</div>`;
        },
      },
      xAxis: monthAxis(f.months.map((m) => m.month)),
      yAxis: moneyAxis(fmtCompact),
      series: [
        // the band is its low edge plus its width stacked on it. ECharts stacks a value only on values of its own sign
        // by default, so once the low edge falls below zero the width would stack on zero and the band would stay
        // above it while the line falls; "all" stacks the width on the low edge whatever its sign, so the band follows
        // the line below zero and the axis holds both
        ...(band ? [{ name: "low", type: "line", stack: "band", stackStrategy: "all", symbol: "none",
          lineStyle: { opacity: 0 }, data: f.months.map((m) => Math.round(m.low)) },
        { name: "range", type: "line", stack: "band", stackStrategy: "all", symbol: "none",
          lineStyle: { opacity: 0 }, areaStyle: { color: "#3b82f6", opacity: 0.12 },
          data: f.months.map((m) => Math.round(m.high - m.low)) }] : []),
        { name: "Projected liquid cash", type: "line", color: "#3b82f6", symbolSize: 4,
          data: f.months.map((m) => Math.round(m.cumCash)),
          ...(shownFloor !== null ? {
            markLine: {
              silent: true, symbol: "none",
              // inside the plot, above the line's right end: the default (outside, to the right) is clipped
              label: { color: CHART_TEXT, formatter: (p: { name: string; value: number }) => `${p.name}: ${fmtUSD0(p.value)}`, position: "insideEndTop" },
              lineStyle: { color: "#f59e0b", type: "dashed" },
              data: [{ name: "3-mo essentials floor", yAxis: Math.round(shownFloor) }],
            },
          } : {}) },
      ],
    };
  }, [f, shownFloor, band]);
  if (liquidCash === null) return <Card testId="card-forecast" title={FORECAST_TITLE}><NoBalances /></Card>;
  // no cash flow at all is no transaction yet; some, but too little to forecast from, is not enough history yet, in the
  // short form: the page's line says the forecast needs FORECAST_MIN_MONTHS full months and how many there are
  if (!f) {
    return (
      <Card testId="card-forecast" title={FORECAST_TITLE}>
        {data.cashflow.length === 0 ? <NoTransactions /> : <NotEnoughHistoryYet />}
      </Card>
    );
  }
  return (
    <Card testId="card-forecast" title={FORECAST_TITLE}>
      <div className="mb-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Liquid cash today" value={fmtUSD0(liquidCash)} />
        <StatCard label="Baseline income" value={`${fmtUSD0(f.baselineIncome)}/mo`}
          sub="trailing 3 full months" />
        {/* the baseline averages the twelve full months, or the six to eleven there are: the label says which */}
        <StatCard label="Baseline spend" value={`${fmtUSD0(f.baselineSpend)}/mo`}
          sub={`${f.spendMonths === 12 ? "trailing 12 mo" : `trailing ${countOf(f.spendMonths, "full month")}`}, `
            + (f.months.some((m) => m.scheduled > 0)
              ? "excl. scheduled bills; seasonality applied per month"
              : "seasonality applied per month (December runs hotter)")} />
        <StatCard label="Cash floor check"
          value={!hasFloor ? "—" : f.floorBreachMonth ? fmtMonth(f.floorBreachMonth) : "clear"}
          tone={!hasFloor ? undefined : f.floorBreachMonth ? "warn" : "pos"}
          sub={floor === null
            ? NO_TRANSACTIONS_YET
            : !hasFloor
              ? "no essential row ticked — the floor is undefined (toggles in the gauge below)"
              : f.floorBreachMonth
                ? "low band dips under 3 months of essentials"
                : "stays above 3 months of essentials all year"} />
      </div>
      <EChart option={option} height={260} />
      <p data-testid="forecast-caption" className="mt-1 text-[10px] text-faint">
        {forecastCaption(f, band, hasFloor, asOf)}
        {f.months.some((m) => m.scheduled > 0) && " Auto-detected recurring schedules are not in the baseline: they are counted once, at their expected dates (see the tooltip's scheduled figure)."}
      </p>
    </Card>
  );
}

/** whether the snapshot holds a home (a House-class value), which the stress test's home slider and the net-worth
 *  projection's word on house appreciation are about: with none, neither is shown */
const holdsHouse = (s: Snapshot): boolean => (s.by_class[HOUSE_CLASS] ?? 0) !== 0;

/** what a market drawdown does to net worth, by shocking each asset class; `avgMonthlySavings` (the savings pace) is null
 *  with too little history (no pace to count the years of saving at: the tile reads "—", the page's line says why), and
 *  a pace at or below none counts no years either (the tile says income did not cover spending); a positive pace counts
 *  them at its real figure, which the tile names */
export function MarketShockCard({ avgMonthlySavings }: { avgMonthlySavings: PaceSeed | null }) {
  const { data } = useData();
  const cur = useSnapshots().latest;
  const gap = useMemo(() => runRateGap(data), [data]);
  const defaults: Record<string, number> = { ...SHOCK_DEFAULTS };
  const [vals, setVals] = useState<Record<string, number>>({ ...defaults });
  const title = "Stress test — market drawdown";
  // no snapshot: no net worth to shock, said in short (the forecast card above says it in full). The bear-case KPI is no
  // button then, but a `?card=` link can still name this card, so it keeps its anchor
  if (cur === null) {
    return <Card testId="card-market-shock" id={SCROLL_TARGETS.bearCase} title={title}><NoBalances short /></Card>;
  }
  const fields: AssumptionField[] = [
    { key: "eq", label: "Stocks / retirement / 529", min: -60, max: 0, step: 5, unit: "%",
      source: "2008 was about −50% peak-to-trough; 2022 about −25%" },
    { key: "crypto", label: "Crypto", min: -100, max: 0, step: 5, unit: "%" },
    { key: "priv", label: "Private stock", min: -100, max: 0, step: 5, unit: "%",
      source: "private valuations are company-set; in a downturn marks can go to zero" },
    // a home's value slider only for a household with a House row: with none it moves nothing
    ...(holdsHouse(cur) ? [{ key: "house", label: "Home value", min: -40, max: 0, step: 5, unit: "%" }] : []),
  ];
  const newTotal = shockedTotal(cur, vals as Shocks);
  const delta = newTotal - cur.total;
  // a loss needs something held that the sliders move: a class the sliders as set move, or one the bear case moves (so
  // sliders moved to no fall over stocks are a true $0 loss). With neither (cash alone; a home whose slider is at 0%)
  // there is no loss to name and none to make up, whatever the savings pace
  const moves = holdsShockable(cur, vals as Shocks) || holdsShockable(cur, SHOCK_DEFAULTS);
  const nothingMoves = holdsShockable(cur) ? "nothing held that the sliders move as set" : "nothing held that the sliders move";
  // the note names a mortgage only for a household that has one: a mortgage account, or a Mortgage-class row
  const hasMortgage = (data.assumptions.mortgage ?? null) !== null || (cur.by_class["Mortgage"] ?? 0) !== 0;
  const pace = avgMonthlySavings?.pace ?? null;
  const yearsOfSaving = pace !== null && pace > 0 ? -delta / (pace * 12) : 0;
  const over = avgMonthlySavings === null ? null : fromMonths(avgMonthlySavings.months);
  const yearsSub = avgMonthlySavings === null ? gapShort(gap)
    : !moves ? "no loss to make up"
    : avgMonthlySavings.pace <= 0 ? `no savings pace: income ${noSurplus(avgMonthlySavings)}`
    : over === null ? `at your actual ${fmtUSD0(avgMonthlySavings.pace)}/mo savings pace`
    : `at your ${fmtUSD0(avgMonthlySavings.pace)}/mo savings pace, ${over}`;
  return (
    <Card testId="card-market-shock" id={SCROLL_TARGETS.bearCase} title={title}>
      <AssumptionsPanel fields={fields} values={vals} defaults={defaults} onChange={setVals} />
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {/* a warning tone needs a loss; with nothing held that the sliders move there is no loss to name at all */}
        <StatCard label="Net worth after shock" value={fmtUSD0(newTotal)} tone={delta <= -0.5 ? "warn" : undefined} />
        {moves
          ? <StatCard label="Paper loss" value={fmtDelta(delta)} tone={delta <= -0.5 ? "neg" : undefined}
              sub="debt balances unchanged — leverage cuts both ways" />
          : <StatCard label="Paper loss" value="—" sub={nothingMoves} />}
        <StatCard label="≈ Years of saving" value={yearsOfSaving > 0 ? yearsOfSaving.toFixed(1) : "—"} sub={yearsSub} />
      </div>
      {pace === null && gap.noTransactions && <div className="mt-2"><NoTransactions short /></div>}
      <p data-testid="market-shock-note" className="mt-2 text-[10px] text-faint">
        Only the classes the sliders name move: {hasMortgage ? "cash, the mortgage and every other debt" : "cash and every debt"}{" "}
        stay as they are. The point isn't prediction — it's checking the household survives the bad year without selling
        at the bottom (see the emergency fund above).
      </p>
    </Card>
  );
}

/** How far the FIRE line looks ahead, and how far the projection runs when its person has no age to retire at. */
const HORIZON_YEARS = 40;

/** the retirement age the projection's slider starts at: built in, no configuration key states one */
const RETIRE_AGE_START = 65;
// a tag's title never starts with its field's label: a field is found by the label its own title starts with
const AGE_TITLE = `The built-in starting age of ${RETIRE_AGE_START}, not one the household stated. Move the slider to set one.`;

/** "Alex", "Alex and Sam", "Alex, Sam and Robin" */
const andList = (names: readonly string[]): string =>
  names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;

/** where a retirement account is mapped to the person it belongs to, as a hint after the sentence that needs it */
const RETIREMENT_KEY_HINT = (
  <span className="text-zinc-500">
    (<Code>retirement_accounts:</Code> under <Code>household:</Code> in <Code>assumptions.yaml</Code>)
  </span>
);

/** The retirement projection, per adult or combined. It projects only the adults with a retirement account mapped to
 *  them (household.retirement_accounts) that has reported a balance (`reported`): an adult with none mapped, or whose
 *  mapped accounts have reported nothing yet, has a "—" balance tile and no contribution field, no contribution of
 *  theirs feeds the projection, and the card says who is left out and why (with the key that maps an account); with no
 *  adult to project there is no projection to draw and the card says so in its place. An adult without a birth year
 *  (household.yaml leaves it out) keeps their contribution field and balance tile but has no age: projecting for them
 *  draws calendar years over the full horizon instead of their age up to the retirement age, the combined view's age
 *  axis is the first projected adult who has an age (calendar years when none has), and an age the card cannot know
 *  prints as "—". With ONE adult projected the card is that adult's (its title names them, there is no Combined /
 *  name toggle, and the crossing says whose accounts it counts and who has none); an adult some of whose accounts have
 *  reported nothing yet has those accounts named as such in the tile. A projected adult's monthly contribution starts
 *  unset (no configuration key states one): their projection is today's balance growing alone, and the line under the
 *  chart and the crossing say so, until the person sets an amount; an amount typed lasts for the visit (no key keeps
 *  it), which the line says too. A value nobody stated carries a "default" tag (components/DefaultTag.tsx) beside its
 *  control and beside each figure that rests on it: the withdrawal rate when neither the configuration nor the app
 *  states one (the FIRE number and crossing rest on it; until the person sets the slider), the return scenarios when
 *  the configuration leaves them out (the line under the chart, and the crossing, which grows at their middle), and
 *  the built-in retirement age while it shapes the projection (some projected adult has a birth year). The crossing's
 *  growth is nominal against a FIRE number in today's dollars, and its line says so. */
export function RetirementCard({ hh, year, retirementBy, reported, reportedAccounts, retirementAsOf, balanceOnly, coreSpend }: {
  hh: Household; year: number; retirementBy: Record<string, number>;
  /** the adults some of whose retirement accounts reported a holding or a balance ($0 included; lib/retirement.ts) */
  reported: readonly string[];
  /** the mapped retirement accounts that reported a holding or a balance */
  reportedAccounts: readonly string[];
  retirementAsOf: Record<string, string>;
  /** retirement accounts counted from a feed balance because they report no positions */
  balanceOnly: string[];
  /** a year of core spend (emergency.ts trailingYearCoreSpend: the twelve full months before the month in progress, or
   *  fewer annualised); null with no transaction, or with fewer full months than RUN_RATE_MIN_MONTHS, when there is no
   *  FIRE number to draw or cross (the tiles say which, the line under them why) */
  coreSpend: CoreSpend | null;
}) {
  const { data, prefs } = useData();
  const vocab = useVocab();
  const gap = useMemo(() => runRateGap(data), [data]);
  // what the FIRE number's year of spending is: every spending row but the one-off money movements (emergency.ts
  // trailingYearCoreSpend), named here by their own names
  const coreWords = `spending less ${andList(vocab.moneyMovementLabels())}`;
  const adults = hh.people.filter((p) => p.role === "owner");
  const accountsOf = (who: string) =>
    Object.entries(hh.retirement_accounts).filter(([, p]) => p === who).map(([a]) => a);
  // the adults the card projects: those with a retirement account mapped to them that has reported a balance; a
  // contribution to an account that does not exist, or growth on a balance nobody has seen, is no projection
  const mapped = (name: string) => accountsOf(name).length > 0;
  const owners = adults.filter((o) => mapped(o.name) && reported.includes(o.name));
  const unmapped = adults.filter((o) => !mapped(o.name)).map((o) => o.name);
  const unreported = adults.filter((o) => mapped(o.name) && !reported.includes(o.name)).map((o) => o.name);
  // whether the data holds a retirement account at all (one mapped, an account or a holding of the Retirement class):
  // with none, there is nothing to map yet, and the card says what brings one rather than asking for a mapping
  const anyRetirement = Object.keys(hh.retirement_accounts).length > 0
    || data.accounts.some((a) => a.asset_class === RETIREMENT_CLASS) || data.holdings.some((h) => h.asset_class === RETIREMENT_CLASS);
  const noneMapped = anyRetirement ? "no retirement account mapped" : "no retirement account yet";
  // ONE adult projected: the card is that adult's, and its crossing says who else is left out and why
  const solo = owners.length === 1 ? owners[0].name : null;
  const leftOut = [
    ...(unmapped.length ? [`${andList(unmapped)} ${unmapped.length === 1 ? "has" : "have"} none mapped`] : []),
    ...(unreported.length ? [`${andList(unreported)}: no balance reported yet`] : []),
  ].join("; ");
  const monthlyKey = (who: string) => `monthly:${who}`;
  /** an adult's age at `year`; null without a birth year */
  const ageOf = (name: string): number | null => {
    const born = owners.find((o) => o.name === name)?.birth_year;
    return born === undefined ? null : year - born;
  };
  const aged = owners.find((o) => o.birth_year !== undefined);   // the first projected adult with an age
  // no contribution is assumed: no configuration key states one, so each adult's starts UNSET (an empty box, the slider
  // at none) and their projection is today's balance growing alone until the person sets an amount
  const defaults = {
    retireAge: RETIRE_AGE_START, wr: data.assumptions.projections.withdrawal_rate_pct,
    ...Object.fromEntries(owners.map((o) => [monthlyKey(o.name), 0])),
  };
  const [vals, setVals] = useState<Record<string, number>>({ ...defaults });
  const [who, setWho] = useState<string>("Combined");
  const { touched, touch, untouch, forget } = useTouched();
  // a value nobody stated: the withdrawal rate when neither the configuration nor the app states it (until the person
  // sets the slider), the return scenarios and the crossing's growth seeded from them when the configuration leaves them
  // out, and the built-in retirement age while it shapes the projection (some projected adult has a birth year)
  const wrDefault = !touched.has("wr")
    && isProjectionDefault(data.assumptions.projections_stated, prefs.doc.projections, "withdrawal_rate_pct");
  const ageDefault = !touched.has("retireAge") && aged !== undefined;
  const scenarios = data.assumptions.projections.return_scenarios_pct;
  const growth = useGrowthSeed();
  const returnsDefault = growth.isDefault;
  const unsetContrib = owners.map((o) => o.name).filter((n) => !touched.has(monthlyKey(n)));
  const wrTitle = `The built-in default withdrawal rate of ${data.assumptions.projections.withdrawal_rate_pct}%, not one the`
    + " household stated. Set it on Data → Projection defaults.";
  const crossingTitle = [wrDefault && `Rests on the default withdrawal rate. ${wrTitle}`,
    returnsDefault && `Grows at ${growth.pct}% a year: ${growth.title}`].filter(Boolean).join(" ");

  const axisName = who === "Combined" ? (aged?.name ?? null) : who;
  const axisAge = axisName === null ? null : ageOf(axisName);
  const years = axisAge === null ? HORIZON_YEARS : Math.max(1, vals.retireAge - axisAge);
  const fields: AssumptionField[] = [
    // the retirement age shapes the projection only: with no adult to project there is none to shape
    ...(owners.length === 0 ? [] : [{ key: "retireAge", label: "Retirement age", min: 50, max: 70, step: 1, unit: "yr",
      ...(axisAge === null
        ? { source: `needs a birth year in the household config; without one the projection runs ${HORIZON_YEARS} years` }
        : {}),
      tag: ageDefault ? <DefaultTag title={AGE_TITLE} /> : undefined }]),
    ...owners.map((o): AssumptionField => ({
      key: monthlyKey(o.name), label: `${o.name} contrib`, min: 0, max: 5000, step: 100, unit: "$",
      source: "Pre-tax retirement contributions never reach bank data, so none is assumed: set the monthly amount to count "
        + "one (it lasts for this visit)" })),
    { key: "wr", label: "Withdrawal rate", min: 3, max: 5, step: 0.25, unit: "%",
      source: "Trinity-study rule of thumb (4%)", tag: wrDefault ? <DefaultTag toData title={wrTitle} /> : undefined },
  ];
  const sumOwners = (m: Record<string, number>, key: (name: string) => string = (n) => n) =>
    owners.reduce((s, o) => s + (m[key(o.name)] ?? 0), 0);
  const principal = who === "Combined" ? sumOwners(retirementBy) : (retirementBy[who] ?? 0);
  const monthlyAll = sumOwners(vals, monthlyKey);
  const monthly = who === "Combined" ? monthlyAll : (vals[monthlyKey(who)] ?? 0);
  // the adults the view draws whose contribution is unset: their part is today's balance growing alone, and it says so
  const shownUnset = unsetContrib.filter((n) => who === "Combined" || n === who);
  const shownCount = who === "Combined" ? owners.length : 1;
  const contribWords = shownUnset.length === 0 ? "with the monthly contributions set above"
    : shownUnset.length === shownCount
      ? `from today's ${shownCount === 1 ? "balance" : "balances"} growing alone: no monthly contribution is set`
      : `with no monthly contribution set for ${andList(shownUnset)}`;

  // FIRE is household-level: combined balances + contributions at the middle return scenario (useGrowthSeed); no FIRE
  // number without a year of spending: the tiles say "No transactions yet." when there is none, and read "—" alone with
  // too few full months (the page's line says why)
  const fire = coreSpend === null ? null : fireNumber(coreSpend.annual, vals.wr);
  const noFire = gapShort(gap);
  const coreOver = coreSpend === null ? null : fromMonths(coreSpend.months);
  const crossingLabel = `FIRE crossing @${growth.pct}%`;
  const seriesFire = useMemo(() => projectGrowth({
    principal: sumOwners(retirementBy), monthlyContribution: monthlyAll,
    annualReturnPct: growth.pct, years: HORIZON_YEARS,
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [retirementBy, monthlyAll, growth.pct]);
  const crossing = fire === null ? null : fireCrossing(seriesFire, fire);
  const fireAge = aged ? ageOf(aged.name) : null;   // the crossing is told at the first aged adult's age

  const retirement = useMemo(() => {
    // one line per return scenario: with none stated there is nothing to draw (the card says so)
    if (scenarios.length === 0) return null;
    const runs = scenarios.map((r) => ({
      r,
      pts: projectGrowth({ principal, monthlyContribution: monthly, annualReturnPct: r, years })
        .filter((pt) => pt.month % 12 === 0),
    }));
    // the x axis: the person's age, or the calendar year when the card has no age for them
    const ticks = runs[0].pts.map((pt) => String((axisAge ?? year) + pt.month / 12));
    const axisLabel = axisAge === null ? "Year" : "Age";
    // the chart's lines, built once: the series below draw these arrays and the view-data table lists them
    const growth = runs.map(({ r, pts }) => ({ name: `${r}%`, data: pts.map((pt) => Math.round(pt.balance)) }));
    const fireLine = who === "Combined" && fire !== null
      ? { name: `FIRE number (${vals.wr}% WR)`, data: runs[0].pts.map(() => Math.round(fire)) } : null;
    // the table's headers say what the legend's bare "5%" means: the assumed annual return
    const asColumns = growth.map((g) => ({ ...g, name: `${g.name} return` }));
    const table = { ...categoryTable(axisLabel, ticks, fireLine ? [...asColumns, fireLine] : asColumns),
      csvName: "retirement-projection.csv", label: "retirement projection" };
    const option = {
      grid: baseGrid,
      legend: baseLegend(),
      tooltip: moneyTooltip(),
      xAxis: { type: "category", data: ticks,
        axisLabel: { color: CHART_TEXT },
        name: axisAge === null ? axisLabel : `${axisName}'s age`,
        nameTextStyle: { color: CHART_TEXT } },
      yAxis: moneyAxis(fmtCompact),
      series: [
        ...growth.map(({ name, data }, i) => ({
          name, type: "line" as const, showSymbol: false,
          lineStyle: { width: i === 1 ? 2.5 : 1 },
          color: ["#64748b", "#3b82f6", "#22c55e"][i],
          areaStyle: i === 1 ? { opacity: 0.08 } : undefined,
          data,
        })),
        ...(fireLine ? [{
          name: fireLine.name, type: "line" as const, color: "#f59e0b",
          showSymbol: false, lineStyle: { type: "dashed" as const },
          data: fireLine.data }] : []),
      ],
    };
    return { option, table };
  }, [principal, monthly, years, scenarios, fire, vals.wr, who, axisName, axisAge, year]);

  return (
    <Card testId="card-retirement"
      title={`Retirement projection — ${solo !== null ? `${solo}'s retirement accounts`
        : who === "Combined" ? "combined retirement accounts" : `${who}'s retirement accounts`}`}
      right={owners.length > 1 ? <SegmentToggle label="Whose accounts" value={who}
        options={[{ value: "Combined", label: "Combined" },
          ...owners.map((o) => ({ value: o.name, label: o.name }))]}
        onChange={setWho} /> : undefined}>
      <div className="mb-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {adults.map((o) => {
          const label = `${o.name} retirement today`;
          if (!mapped(o.name)) return <StatCard key={o.name} label={label} value="—" sub={noneMapped} />;
          if (!reported.includes(o.name)) {
            return <StatCard key={o.name} label={label} value="—" sub={`${accountsOf(o.name).join(" + ")} · no balance reported yet`} />;
          }
          // the total is the reporting accounts'; one that has reported nothing is named as such, not summed as $0
          const silent = accountsOf(o.name).filter((a) => !reportedAccounts.includes(a));
          return (
            <StatCard key={o.name} label={label} value={fmtUSD0(retirementBy[o.name] ?? 0)}
              sub={accountsOf(o.name).filter((a) => reportedAccounts.includes(a)).join(" + ")
                + (retirementAsOf[o.name] ? ` (as of ${fmtDate(retirementAsOf[o.name])})` : "")
                + (silent.length ? ` · ${andList(silent)}: no balance reported yet` : "")} />
          );
        })}
        {coreSpend === null || fire === null ? (
          <>
            <StatCard label="FIRE number" value="—" sub={noFire} />
            <StatCard label={crossingLabel} value="—" sub={noFire} />
          </>
        ) : (
          <>
            <StatCard label="FIRE number" value={fmtUSD0(fire)}
              tag={wrDefault ? <DefaultTag toData title={`Rests on the default withdrawal rate. ${wrTitle}`} /> : undefined}
              sub={coreOver === null
                ? `trailing-12mo ${coreWords}: ${fmtUSD0(coreSpend.annual)} ÷ ${vals.wr}%`
                : `${coreWords}: ${fmtUSD0(coreSpend.annual)} a year, ${coreOver}, annualised, ÷ ${vals.wr}%`} />
            {owners.length === 0
              ? <StatCard label={crossingLabel} value="—"
                  sub={unreported.length > 0 ? "no retirement balance reported yet" : noneMapped} />
              : <StatCard label={crossingLabel}
                  value={crossing === null ? `>${HORIZON_YEARS}yr`
                    : fireAge === null ? "—" : `age ${Math.round(fireAge + crossing / 12)}`}
                  tag={crossingTitle ? <DefaultTag toData title={crossingTitle} /> : undefined}
                  sub={(solo !== null
                    ? `${solo}'s retirement accounts alone${leftOut ? ` (${leftOut})` : ""} — taxable assets would move this earlier`
                    : "retirement accounts alone — taxable assets would move this earlier")
                    + (unsetContrib.length === 0 ? ""
                      : unsetContrib.length === owners.length ? "; growth alone, no monthly contribution set"
                      : `; no monthly contribution set for ${andList(unsetContrib)}`)
                    + ". The growth is not adjusted for inflation while the FIRE number is in today's dollars, so the real"
                    + " crossing is later."} />}
          </>
        )}
      </div>
      <AssumptionsPanel fields={fields} values={vals} defaults={defaults} onChange={setVals}
        unset={unsetContrib.map(monthlyKey)} onSet={touch}
        onClear={(k) => { untouch(k); setVals((v) => ({ ...v, [k]: 0 })); }} onReset={forget} />
      {owners.length === 0 ? (
        <div className="mt-3">
          <EmptyState>
            {unreported.length > 0
              ? <>No retirement account has reported a balance yet, so there is nothing to project: the projection appears
                  here once one does.</>
              : !anyRetirement
                ? <>No retirement account in the data yet. One arrives with an account whose class is Retirement; map it to
                    the person it belongs to and the projection appears here {RETIREMENT_KEY_HINT}.</>
                : <>No retirement account is mapped to anyone yet, so there is nothing to project: map each one to the
                    person it belongs to and the projection appears here {RETIREMENT_KEY_HINT}.</>}
          </EmptyState>
        </div>
      ) : growth.none || retirement === null ? (
        <div data-testid="retirement-no-scenarios" className="mt-3">
          <EmptyState>
            No return scenario is stated, so there is no projection to draw: state at least one yearly return and it
            appears here (<Code>return_scenarios_pct:</Code> under <Code>projections:</Code> in <Code>assumptions.yaml</Code>).
          </EmptyState>
        </div>
      ) : (
        <>
          <EChart option={retirement.option} tableData={retirement.table} height={320} />
          <p data-testid="retirement-basis" className="mt-1 text-[10px] text-faint">
            Drawn {axisAge === null ? `over ${HORIZON_YEARS} years` : <>to retirement at {vals.retireAge}{ageDefault && axisAge !== null
              && <DefaultTag title={AGE_TITLE} />}</>}, at {andList(scenarios.map((r) => `${r}%`))} a year
            {returnsDefault && <DefaultTag toData title={growth.title} />}, {contribWords}. An amount typed in a contribution
            box lasts for this visit.
          </p>
          {unmapped.length > 0 && (
            <p className="mt-1 text-[10px] text-faint">
              {andList(unmapped)} {unmapped.length === 1 ? "has" : "have"} no retirement account mapped, so the projection
              leaves {unmapped.length === 1 ? "that balance and contribution" : "their balances and contributions"} out
              until one is {RETIREMENT_KEY_HINT}.
            </p>
          )}
          {unreported.length > 0 && (
            <p className="mt-1 text-[10px] text-faint">
              {andList(unreported)}: no retirement balance reported yet, so the projection leaves{" "}
              {unreported.length === 1 ? "that person" : "them"} out until one is.
            </p>
          )}
        </>
      )}
      {balanceOnly.length > 0 && (
        <p className="mt-1 text-[10px] text-faint">
          {balanceOnly.join(", ")}: counted from the feed's balance — the account reports no positions.
        </p>
      )}
    </Card>
  );
}

/** the net-worth projection's starting horizon and the cut's starting amount: what-if inputs, like the stress test's
 *  shocks, which the figures resting on them name in words ("for 30 years", "cutting $500 a month") */
const HORIZON_START_YEARS = 30;
const CUT_START = 500;

/** The net worth projection. Its monthly savings is SET when it starts from a positive savings pace (`prefill`, seeded at
 *  the pace rounded to the slider's step) or when the person sets it - the slider moved or an amount typed, none
 *  included (growth alone); until then its box is empty, its slider at its minimum, and the chart waits, with one line
 *  saying to set an amount: there is no pace yet (too little history), or the pace is at or below none (income did not
 *  cover spending), which the line says. Emptying the box unsets it again; "reset" returns to the seed (or to unset).
 *  A seed is never called "your actual"; the real average is named beside it. The return starts at the household's
 *  middle return scenario (useGrowthSeed): its control and the line under the chart carry "default" while that key is
 *  unstated and the person has not set the slider. The horizon is a what-if input the line names in words. */
function NetWorthCard({ total, house, prefill, gap, year }: {
  /** `house`: the snapshot holds a House-class row, so the growth's one return carries the home's appreciation too */
  total: number; house: boolean; prefill: PaceSeed | null; gap: RunRateGap; year: number;
}) {
  const growth = useGrowthSeed();
  const seeded = prefill !== null && prefill.pace > 0;
  const defaults = { monthly: seeded ? prefill.seed : 0, ret: growth.pct, years: HORIZON_START_YEARS };
  const [vals, setVals] = useState<Record<string, number>>({ ...defaults });
  // the projection rests on the monthly savings: the pace, or an amount the person set
  const [projected, setProjected] = useState(seeded);
  const unset = () => { setProjected(false); setVals((v) => ({ ...v, monthly: 0 })); };
  const { touched, touch, forget } = useTouched();
  const retDefault = growth.isDefault && !touched.has("ret");
  const over = prefill === null ? null : fromMonths(prefill.months);
  const average = prefill === null ? "" : over === null
    ? `your actual trailing-12-month average of income − spending (${fmtUSD0(prefill.pace)}/mo)`
    : `your average of income − spending, ${over} (${fmtUSD0(prefill.pace)}/mo)`;
  const fields: AssumptionField[] = [
    { key: "monthly", label: "Monthly savings", min: 0, max: 15000, step: 250, unit: "$",
      source: prefill === null
        ? "not enough history yet to prefill from, so it starts at none: set your own to draw the projection"
        : !seeded
          ? `Income ${noSurplus(prefill)}, so this starts at none.`
          : `prefilled from ${average}, rounded to the slider's ${fmtUSD0(250)} steps` },
    { key: "ret", label: "Return", min: 0, max: 12, step: 0.5, unit: "%",
      source: "starts at the middle of the household's return scenarios",
      tag: retDefault ? <DefaultTag toData title={growth.title} /> : undefined },
    { key: "years", label: "Horizon", min: 5, max: 40, step: 5, unit: "yr" },
  ];
  const option = useMemo(() => {
    const pts = projectGrowth({ principal: total, monthlyContribution: vals.monthly,
      annualReturnPct: vals.ret, years: vals.years }).filter((p) => p.month % 12 === 0);
    return {
      grid: baseGrid,
      tooltip: moneyTooltip(),
      xAxis: { type: "category", data: pts.map((p) => String(year + p.month / 12)),
        axisLabel: { color: CHART_TEXT } },
      yAxis: moneyAxis(fmtCompact),
      series: [{ type: "line", color: "#3b82f6", showSymbol: false,
        areaStyle: { opacity: 0.15 }, data: pts.map((p) => Math.round(p.balance)) }],
    };
  }, [total, vals, year]);
  const end = total * Math.pow(1 + vals.ret / 100, vals.years);
  return (
    <Card testId="card-networth-projection" title={`Net worth projection — from ${fmtUSD0(total)} today`}>
      <AssumptionsPanel fields={fields} values={vals} defaults={defaults} onChange={setVals}
        unset={projected ? [] : ["monthly"]} onSet={(k) => { touch(k); if (k === "monthly") setProjected(true); }}
        onClear={(k) => { if (k === "monthly") unset(); }} onReset={() => { forget(); setProjected(seeded); }} />
      {projected ? <EChart option={option} height={240} /> : (
        <div className="my-3 space-y-1">
          {gap.noTransactions && <NoTransactions short />}
          <EmptyState>
            {prefill !== null && `Income ${noSurplus(prefill)}, so savings start at none: `}
            {prefill !== null ? "set" : "Set"} a monthly amount above to draw the projection.
          </EmptyState>
        </div>
      )}
      <p data-testid="networth-projection-basis" className="text-[10px] text-faint">
        {house ? "Growth applies to the full net worth incl. house (house appreciation folded into the single return knob)."
          : "Growth applies to the full net worth."} It grows at {vals.ret}% a year{retDefault && <DefaultTag toData title={growth.title} />}{" "}
        for {vals.years} years; with no savings it reaches {fmtCompact(end)}.
      </p>
    </Card>
  );
}

/** the emergency-fund gauge's dial, in months of essential spending */
const EFUND_GAUGE_MAX = 12;

/** The emergency-fund gauge: liquid cash over essential spending. With no snapshot (`liquidCash` null), no transaction,
 *  or too few full months to average (fewer than agg.ts RUN_RATE_MIN_MONTHS), the card says which is missing instead of
 *  a gauge, the no-snapshot line in short when a card above it says it in full (`shortNoBalances`); it keeps its anchor
 *  for a `?card=` link (the runway KPI scrolls here once the data is there). */
export function EmergencyFund({ liquidCash, shortNoBalances = false }: { liquidCash: number | null; shortNoBalances?: boolean }) {
  const { data } = useData();
  const vocab = useVocab();
  // device-local toggles, shared live with the Planning header's runway KPI (lib/efundOverrides)
  const overrides = useEfundOverrides();
  // the six full months before the month in progress, or as many as there are (lib/emergency.ts emergencyWindow), as the
  // runway KPI reads
  const inProgress = useMemo(() => monthInProgress(data), [data]);
  const gap = useMemo(() => runRateGap(data), [data]);
  const { rows, window, essential, everything, months, monthsAll } = useMemo(
    () => emergencyCoverage(vocab, data.transactions,
      emergencyWindow(data.cashflow, inProgress, dataFirstDay(data.transactions)), liquidCash, overrides),
    [vocab, data.transactions, data.cashflow, inProgress, liquidCash, overrides]);
  const isOn = (r: SpendRow) => overrideFor(overrides, r.category, r.subcategory) ?? r.essential;
  const counted = rows.filter(isOn).length;
  // closed unless nothing is counted yet (decided once, on arrival: ticking a row must not fold the list under the finger)
  const [checklistOpen, setChecklistOpen] = useState(months === null && rows.length > 0);
  const toggle = (r: SpendRow) => writeEfundOverrides(toggleEssential(overrides, r));
  const reset = () => writeEfundOverrides(null);
  const mo = (m: number | null) => fmtMonths(m, "");
  // past twelve months the needle is pinned at the dial's end, whose label reads "12+", and the text below says the value
  const reading = gaugeReading(months, EFUND_GAUGE_MAX);

  const option = {
    series: [{
      type: "gauge", min: 0, max: EFUND_GAUGE_MAX, splitNumber: 4, startAngle: 210, endAngle: -30,
      axisLine: { lineStyle: { width: 18, color: [[0.25, "#ef4444"], [0.5, "#f59e0b"], [1, "#22c55e"]] } },
      // no essential spending to divide by: no needle and "—", never a needle parked on 0; a needle shorter than the
      // label ring, so one pinned at the end never covers the "12+" it points at
      pointer: { show: months !== null, length: "42%", itemStyle: { color: CHART_STRONG } },
      axisTick: { show: false }, splitLine: { length: 12, lineStyle: { color: CHART_SURFACE } },
      axisLabel: { color: CHART_TEXT, distance: 16, fontSize: 10, formatter: reading.tick },
      // below the open arc, clear of the 0 and 12 end labels
      detail: { color: CHART_STRONG, fontSize: 22, offsetCenter: [0, "60%"],
        formatter: () => fmtMonths(months) },
      data: [{ value: reading.needle }],
    }],
  };

  const title = "Emergency fund — months of ESSENTIAL spending covered by cash";
  if (liquidCash === null || window === null || essential === null || everything === null) {
    return (
      <Card testId="card-emergency-fund" id={SCROLL_TARGETS.runway} title={title}>
        {liquidCash === null ? <NoBalances short={shortNoBalances} /> : <GapLine gap={gap} />}
      </Card>
    );
  }
  return (
    <Card testId="card-emergency-fund" id={SCROLL_TARGETS.runway} title={title}
      right={<button onClick={reset} className="text-xs text-zinc-500 underline">reset toggles</button>}>
      <EChart option={option} height={210} />
      <p className="text-center text-xs text-zinc-400">
        {fmtUSD0(liquidCash)} cash ÷ {fmtUSD0(essential)}/mo essentials ={" "}
        <b>{mo(months)} months</b>
        <span className="text-faint"> · at full current lifestyle ({fmtUSD0(everything)}/mo): {mo(monthsAll)} mo</span>
        {months === null && (
          <span className="block text-amber-400">
            {rows.length ? "no row is marked essential — tick what you'd keep paying" : "no spending history in this window yet"}
          </span>
        )}
      </p>
      <p className="mb-2 mt-1 text-center text-[10px] text-faint">
        basis: {fmtMonth(window.from)} → {fmtMonth(window.to)} averages ({countOf(window.months, "full month")}), so expenses that have
        ended are already gone — toggle what you'd actually keep paying in an emergency
      </p>
      {/* The checklist is one disclosure: closed, it is a line that says how many rows count ("Essentials: 23 of 53 counted -
          edit") and the gauge and the sentence above stay in view; open, it is every row at every width, uncapped - the ticked
          rows ARE the headline essentials figure, so none may hide behind a scroll box (a phone used to show 9 of 18 with no
          cue); a long name ends in an ellipsis with the full name in its title instead of being cut mid-word. The toggles are
          this device's saved ones (lib/efundOverrides), not the disclosure's state, so closing it loses nothing. It opens by
          itself when nothing is counted: the list is then the way out of the amber hint above. */}
      <details open={checklistOpen} onToggle={(e) => setChecklistOpen(e.currentTarget.open)} data-testid="efund-essentials"
        className="mt-1 rounded-lg border border-zinc-800">
        <summary className="flex min-h-[40px] cursor-pointer select-none items-center gap-2 px-3 py-1 text-xs text-zinc-300 sm:min-h-0">
          <span>Essentials: {counted} of {rows.length} counted — <span className="text-blue-400">{checklistOpen ? "hide" : "edit"}</span></span>
        </summary>
        <div data-testid="efund-rows" className="grid grid-cols-1 gap-x-4 px-3 sm:grid-cols-2">
          {rows.map((r) => {
            const on = isOn(r);
            // an uncounted row is dimmer, never faint: zinc-400 text keeps its contrast (opacity-45 read at about 2:1)
            return (
              <label key={r.key} title={`${r.category} / ${r.subcategory}`}
                className={`flex min-h-[40px] cursor-pointer items-center justify-between gap-2 border-b border-zinc-800/50 py-1 text-xs sm:min-h-0 ${on ? "" : "text-zinc-400"}`}>
                <span className="flex min-w-0 items-center gap-2">
                  <input type="checkbox" checked={on} onChange={() => toggle(r)}
                    className="shrink-0 accent-emerald-500" />
                  <span className={`min-w-0 ${PHONE_WRAP}`}>
                    {r.category} <span className={on ? "text-zinc-500" : "text-zinc-400"}>/ {r.subcategory}</span>
                  </span>
                </span>
                <span className={`shrink-0 tabular-nums ${on ? "text-zinc-300" : "text-zinc-400"}`}>{fmtUSD0(r.monthly)}/mo</span>
              </label>
            );
          })}
        </div>
        <p className="px-3 py-1 text-[10px] text-faint">
          {rows.length} spending rows · {counted} counted as essential
        </p>
      </details>
      <SplitNote categories={rows.map((r) => r.category)} />
    </Card>
  );
}

/** What a monthly spending cut grows to. `spendingKnown` is false with too little history to know what the household
 *  spends: the cut then starts UNSET - an empty box, the slider at its minimum - and the tiles read "—" with one line
 *  saying to set a cut; what holds them back is the unset cut, so any cut the person sets (the slider moved or an amount
 *  typed, none included) draws them. Emptying the box unsets it again; "reset" returns to the start. `total` (the newest
 *  net worth) is null with no snapshot, and the share of it each figure is left out then and whenever it is not above
 *  zero (no share of a total of nothing). The cut is a what-if input the line under the tiles names in words; the return
 *  starts at the household's middle return scenario (useGrowthSeed), and its control and the tiles carry "default"
 *  while that key is unstated and the person has not set the slider. */
function SpendingCutCard({ total, spendingKnown, gap }: { total: number | null; spendingKnown: boolean; gap: RunRateGap }) {
  const growth = useGrowthSeed();
  const defaults = { cut: spendingKnown ? CUT_START : 0, ret: growth.pct };
  const [vals, setVals] = useState<Record<string, number>>({ ...defaults });
  // the tiles rest on the cut: the seed, or an amount the person set
  const [shown, setShown] = useState(spendingKnown);
  const { touched, touch, forget } = useTouched();
  const retDefault = growth.isDefault && !touched.has("ret");
  const fields: AssumptionField[] = [
    { key: "cut", label: "Monthly spending cut", min: 0, max: 5000, step: 100, unit: "$",
      ...(spendingKnown ? {} : { source: "not enough history yet to know what the household spends: set a cut to see what it grows to" }) },
    { key: "ret", label: "Return on savings", min: 0, max: 12, step: 0.5, unit: "%",
      source: "starts at the middle of the household's return scenarios",
      tag: retDefault ? <DefaultTag toData title={growth.title} /> : undefined },
  ];
  const horizons = [10, 20, 30];
  return (
    <Card testId="card-spending-cut" title="What if we cut spending?">
      <AssumptionsPanel fields={fields} values={vals} defaults={defaults} onChange={setVals}
        unset={shown ? [] : ["cut"]} onSet={(k) => { touch(k); if (k === "cut") setShown(true); }}
        onClear={(k) => { if (k === "cut") { setShown(false); setVals((v) => ({ ...v, cut: 0 })); } }}
        onReset={() => { forget(); setShown(spendingKnown); }} />
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {horizons.map((y) => {
          if (!shown) return <StatCard key={y} label={`After ${y} years`} value="—" />;
          const pts = projectGrowth({ principal: 0, monthlyContribution: vals.cut,
            annualReturnPct: vals.ret, years: y });
          const extra = pts[pts.length - 1].balance;
          return (
            <StatCard key={y} label={`After ${y} years`} value={fmtCompact(extra)}
              tag={retDefault ? <DefaultTag toData title={`Rests on the default return. ${growth.title}`} /> : undefined}
              sub={total === null || total <= 0 ? undefined : `${fmtPct(extra / total, 0)} of today's net worth`} tone="pos" />
          );
        })}
      </div>
      {shown && (
        <p data-testid="spending-cut-basis" className="mt-2 text-[10px] text-faint">
          What cutting {fmtUSD0(vals.cut)} a month grows to, invested at {vals.ret}% a year.
        </p>
      )}
      {!shown && (
        <div className="mt-2 space-y-1">
          {gap.noTransactions && <NoTransactions short />}
          <EmptyState>Set a monthly cut above to see what it grows to.</EmptyState>
        </div>
      )}
    </Card>
  );
}

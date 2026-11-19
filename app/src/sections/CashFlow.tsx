// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import DateRangeBar from "../components/DateRangeBar";
import EChart from "../components/EChart";
import { Code, EmptyState, NO_FULL_MONTH, NOT_ENOUGH_HISTORY, NoTransactions } from "../components/NoData";
import RequireData from "../components/RequireData";
import { Card, StatCard } from "../components/ui";
import { useData } from "../data/DataContext";
import { monthBounds, NO_PRESET_REASON, RangePreset } from "../filters/dateRange";
import { spendingLink, spendingPath } from "../filters/filters";
import { useDateRange } from "../filters/useDateRange";
import { coverageMonths, dataBehindIngest, monthInProgress, savingsRate } from "../lib/agg";
import { categoryTable, type ChartTable } from "../lib/chartTable";
import { baseGrid, CHART_GAIN, CHART_STRONG, CHART_TEXT, CHART_TOTAL_RATE, drawnLegend, moneyAxis, moneyAxisBesideRate, moneyTooltip, monthAxis,
  pinRates, rateAxis, wrapLegend } from "../lib/echarts";
import { countOf, fmtCompact, fmtDelta, fmtMonth, fmtPct, fmtUSD0, rateTone } from "../lib/format";
import { belowRateFloor, incomeStreams, monthlyBurn, monthlyNet, printableRate, savedText, savingsKpis, savingsRateLabel,
  shortfallText } from "../lib/lifeSummary";
import { usePointerWords } from "../lib/pointerWords";
import { grossRateSeries, grossSavingsRate, payrollConfigured } from "../lib/payroll";
import { NEGATIVE, streamColor } from "../lib/tokens";

const PRESETS: RangePreset[] = ["6m", "12m", "24m", "all"];

export default function CashFlow() {
  return (
    <RequireData files={["cashflow_monthly.json"]} what="Cash Flow">
      <CashFlowPage />
    </RequireData>
  );
}

function CashFlowPage() {
  const { data, prefs } = useData();
  const navigate = useNavigate();
  // "tap" for a finger, "click" for a mouse; a pinch or the wheel zooms (lib/pointerWords.ts)
  const words = usePointerWords();
  // the month still in progress (lib/agg.ts monthInProgress): no rate reads it, and no month after it is charted
  const inProgress = useMemo(() => monthInProgress(data), [data]);
  const payrollPhases = prefs.doc.payroll.phases;
  const hasPayroll = payrollConfigured(payrollPhases);
  // default to the last 6 months, where account coverage is strongest;
  // month granularity so "6M" means exactly six monthly columns
  const dr = useDateRange("6m", "month");
  // drop the leading months before enough of the cash-flow accounts have begun their history (the export's in_coverage),
  // and any month after the month in progress: a row the bank dated into a month that has not begun is no bar of it
  const allMonths = useMemo(
    () => (inProgress === null ? [] : coverageMonths(data.cashflow).filter((m) => m.month <= inProgress)),
    [data.cashflow, inProgress]);
  // the month in progress, starred on both charts' axes while it is shown (it has its bills still to come)
  const starred = useMemo(() => new Set(inProgress === null ? [] : [inProgress]), [inProgress]);
  // the window's first and last month (YYYY-MM); undefined: open on that side (All)
  const fromYm = dr.range.from?.slice(0, 7);
  const toYm = dr.range.to?.slice(0, 7);
  const months = useMemo(() => allMonths.filter((m) =>
    (!fromYm || m.month >= fromYm) && (!toYm || m.month <= toYm)), [allMonths, fromYm, toYm]);
  // the data is behind: a whole month lies between its newest month and the ingest month (statement files not yet
  // brought up to date), so that month is where the data ends, not a month whose bills are still coming in. The month
  // just before the ingest month is not behind: on the ingest month's first days its rows are still arriving
  const behind = dataBehindIngest(inProgress, data.manifest.generated_at);
  // how the newest month's row is named in the charts' view-data tables
  const newestTag = behind ? "partial" : "in progress";
  const phases = data.assumptions.income_phases;
  // the denominator of a month's accounts_covered: the cash-flow accounts with any transaction, the ones full coverage
  // waits for (an account in no month's accounts_covered has none - a certificate that only reports a balance - and
  // holds no month back, so it is in neither number)
  const accountCount = useMemo(() => new Set(data.cashflow.flatMap((m) => m.accounts_covered)).size, [data.cashflow]);

  // click a bar -> the Spending table filtered to that month (income/expense/invested)
  const drillToSpending = (p: any) => {
    if (p.componentType !== "series") return;   // ignore markArea/markLine clicks
    const m = months[p.dataIndex];
    if (!m || !p.seriesName) return;
    const [from, to] = monthBounds(m.month);
    const patch = p.seriesName === "Income" ? { direction: "income" as const }
      : p.seriesName === "Spending" ? { direction: "expense" as const }
      : p.seriesName === "Invested" ? { types: ["investment" as const] }
      : {};
    navigate(spendingPath({ from, to, ...patch }));
  };

  const main = useMemo(() => {
    const labels = months.map((m) => m.month);
    // the window's first month; with no month in the window there is nothing for a phase band to span
    const first = labels.length ? labels[0] : null;
    // clamp phase bands to the visible window; skip ones entirely outside it
    const bands = first === null ? [] : phases.filter((p) => p.end.slice(0, 7) >= first)
      .map((p) => ({ p, from: p.start.slice(0, 7) < first ? first : p.start.slice(0, 7) }));
    // one label row per person: one person's phases run back to back, but two people's
    // overlap in time, and their centered labels would print on top of each other
    const people = [...new Set(phases.map((p) => p.person))];
    const markAreas: any[] = bands.map(({ p, from }, i) => [
      {
        name: p.phase,
        xAxis: from,
        itemStyle: { color: ["#8b5cf6", "#f59e0b", "#10b981"][i % 3], opacity: 0.07 },
        label: { color: CHART_TEXT, fontSize: 10, formatter: `${p.person}: ${p.phase}`,
          offset: [0, people.indexOf(p.person) * 12] },
      },
      { xAxis: p.end.slice(0, 7) },
    ]);
    // grey hatch for months with incomplete account coverage
    const gapAreas: any[] = months.filter((m) => !m.full_coverage).map((m) => [
      { xAxis: m.month, itemStyle: { color: "#71717a", opacity: 0.06 } },
      { xAxis: m.month },
    ]);
    // The month in progress keeps its income and spending bars and has no savings-rate point: no rate counts it (its
    // bills are still to come), so the rate line ends on the last full month - here, in the table and in the dashed
    // total-rate line alike
    const rates = months.map((m) => (m.month === inProgress ? null : m.savings_rate));
    // The table reads the same `months` the series below do. Spending is drawn downward and listed as the
    // positive amount it is. The savings rate is the raw ratio (the CSV keeps it as a number; the table
    // shows it as a percentage through the column-aware formatter - a ratio is not money, and null reads
    // "—", never 0%). Coverage is the greyed-month shading in words.
    const base = categoryTable("Month", labels.map((m) => (starred.has(m) ? `${m} (${newestTag})` : m)), [
      { name: "Income", data: months.map((m) => m.income) },
      { name: "Spending", data: months.map((m) => m.spend) },
      { name: "Invested", data: months.map((m) => m.invested) },
      { name: "Savings rate", data: rates },
    ]);
    const RATE_COL = base.columns.indexOf("Savings rate");
    const table: ChartTable = { ...base, columns: [...base.columns, "Coverage"],
      rows: base.rows.map((r, i) => [...r, months[i].full_coverage ? "complete" : "incomplete"]),
      format: (v, col) => (col === RATE_COL ? fmtPct(v) : fmtUSD0(v)),
      csvName: "cash-flow-monthly.csv", label: "monthly income, spending and invested",
      note: ["Spending is shown as a positive amount (the chart draws it downward).",
        "Incomplete months have partial account coverage (the greyed months on the chart).",
        ...(!labels.some((m) => starred.has(m)) ? []
          : behind ? ["The data ends in the last month listed, and later transactions have not been read yet; no rate or average counts that month."]
          : ["The month in progress is still filling in; no rate or average counts it."]),
        ...(hasPayroll ? ["The dashed total savings rate (incl. retirement) line is drawn on the chart but not listed here."] : []),
      ].join(" ") };
    // only once payroll facts are configured (prefs → payroll)
    const grossRates = hasPayroll
      ? grossRateSeries(months, payrollPhases).map((r, i) => (months[i].month === inProgress ? null : r)) : null;
    // a rate below the rate axis' foot is drawn at the foot as a downward triangle (pinRates); the tooltip reads the real
    // rates (`real`), and a line under the chart says so when a month is pinned. A line every rated month of which is
    // pinned (a household with no income in the window) would be a row of pins along the foot: it is not drawn and has
    // no legend entry, its rates stay in the tooltip and the table, and the line under the chart says why
    const ratePins = pinRates(rates);
    const grossPins = grossRates === null ? null : pinRates(grossRates);
    const real: Record<string, (number | null)[]> = { "Savings rate": rates,
      ...(grossRates === null ? {} : { "Total savings rate (incl. retirement)": grossRates }) };
    const rateLines = [
      { name: "Savings rate", label: "savings rate", pins: ratePins, color: CHART_STRONG, dashed: false },
      ...(grossPins === null ? [] : [{ name: "Total savings rate (incl. retirement)", label: "total savings rate",
        pins: grossPins, color: CHART_TOTAL_RATE, dashed: true }]),
    ];
    const drawnRates = rateLines.filter((l) => !l.pins.all);
    const undrawn = rateLines.filter((l) => l.pins.all);
    const series = [
      { name: "Income", type: "bar", color: CHART_GAIN, data: months.map((m) => m.income),
        markArea: { silent: true, data: [...markAreas, ...gapAreas] } },
      { name: "Spending", type: "bar", color: NEGATIVE, data: months.map((m) => -m.spend) },
      { name: "Invested", type: "bar", color: "#6366f1", data: months.map((m) => m.invested) },
      ...drawnRates.map((l) => ({ name: l.name, type: "line", yAxisIndex: 1, color: l.color, symbolSize: 4,
        ...(l.dashed ? { lineStyle: { type: "dashed" as const } } : {}), data: l.pins.data })),
    ];
    const bars = months.flatMap((m) => [m.income, -m.spend, m.invested]);
    const option = {
      grid: { ...baseGrid, top: 48 },
      // a rate line with no point yet (no full month in the window) has no legend entry: the legend lists what is drawn
      legend: drawnLegend(series),
      dataZoom: [{ type: "inside" }],
      tooltip: {
        trigger: "axis",
        formatter: (ps: any[]) => {
          // the month the hovered points are in; a point with no month (an empty chart) has nothing to describe
          const m: (typeof months)[number] | undefined = Array.isArray(ps) ? months[ps[0]?.dataIndex] : undefined;
          if (!m) return "";
          const cov = m.full_coverage ? "" :
            `<div style="color:#f59e0b">⚠ incomplete account coverage (${m.accounts_covered.length}/${accountCount} accounts)</div>`;
          // a rate reads its real value, a pinned one included, and a rate line not drawn still has its month's rate
          const rateText = (name: string, i: number) => {
            const rate = real[name]?.[i];
            return rate == null ? "—" : fmtPct(rate);
          };
          const series = ps.map((p) => `<div>${p.marker} ${p.seriesName}: ${p.seriesName in real
            ? rateText(p.seriesName, p.dataIndex) : fmtUSD0(p.value)}</div>`).join("")
            + undrawn.map((l) => `<div>${l.name}: ${rateText(l.name, ps[0].dataIndex)}</div>`).join("");
          // context vs prior month and same month last year, from full history
          const i = allMonths.findIndex((x) => x.month === m.month);
          const prev = i > 0 ? allMonths[i - 1] : undefined;
          const yoy = allMonths.find(
            (x) => x.month === `${Number(m.month.slice(0, 4)) - 1}${m.month.slice(4)}`);
          const net = m.income - m.spend;
          let extra = `<div style="margin-top:4px;border-top:1px solid #3f3f46;padding-top:4px">`
            + `Net: <b>${fmtDelta(net)}</b></div>`;
          if (prev) extra += `<div style="color:#a1a1aa">spend vs ${fmtMonth(prev.month)}: ${fmtDelta(m.spend - prev.spend)}</div>`;
          if (yoy) extra += `<div style="color:#a1a1aa">spend vs ${fmtMonth(yoy.month)}: ${fmtDelta(m.spend - yoy.spend)}</div>`;
          return `<b>${fmtMonth(m.month)}</b>${cov}${series}${extra}`
            + `<div style="color:#71717a;font-size:10px;margin-top:2px">${words.click} a bar to see its transactions</div>`;
        },
      },
      xAxis: monthAxis(labels, starred),
      // the money axis shares the rate axis' grid when that costs the bars nothing (moneyAxisBesideRate); a rate axis
      // with no line on it is not shown
      yAxis: [moneyAxisBesideRate(bars, fmtCompact),
        { ...rateAxis(), show: drawnRates.some((l) => l.pins.data.some((v) => v !== null)) }],
      series,
    };
    return { option, table, pinned: drawnRates.some((l) => l.pins.pinned), undrawn: undrawn.map((l) => l.label) };
  }, [months, allMonths, phases, hasPayroll, payrollPhases, accountCount, starred, inProgress, behind, newestTag, words.click]);

  // the trailing rates use the whole history, independent of the view window, and FULL months only (lib/agg.ts
  // fullMonths): the month in progress never feeds a rate, and the 12-month cash-basis rate is exactly Home's KPI, which
  // links here. The best and the worst month are the window's own: its full months, never the month in progress
  const { t3, t12, best, worst, inWindow, through: lastFull } = useMemo(
    () => savingsKpis(data.cashflow, inProgress, { from: fromYm, to: toYm }), [data.cashflow, inProgress, fromYm, toYm]);
  // each stream's average month over the trailing 3 full months (a stream that began inside them is averaged from its
  // first month), every stream unfolded: the income chart's view-data table ends on this row
  const streamAvg = useMemo(() => incomeStreams(data.cashflow, inProgress, 3, Number.POSITIVE_INFINITY),
    [data.cashflow, inProgress]);

  // Income by source: one stacked band per income subcategory with income in the window - every stream, none folded into
  // "Other", and none that would only add an empty legend entry. The streams are the data's own subcategories: the
  // largest total over the window first (ties by name), each coloured by its place in that order (streamColor). Its
  // view-data table lists the same bands month by month and ends on the streamAvg row for those streams (labelled with
  // the months it averages, in the Period column).
  const income = useMemo(() => {
    const avg = new Map(streamAvg.map((x) => [x.name, x.monthly]));
    const subs = [...new Set(months.flatMap((m) => Object.keys(m.income_by_subcategory)))]
      .filter((s) => months.some((m) => (m.income_by_subcategory[s] ?? 0) !== 0));
    const total = new Map(subs.map((s) => [s, months.reduce((sum, m) => sum + (m.income_by_subcategory[s] ?? 0), 0)]));
    subs.sort((a, b) => (total.get(b) ?? 0) - (total.get(a) ?? 0) || a.localeCompare(b));
    const bands = subs.map((s) => ({ name: s, data: months.map((m) => m.income_by_subcategory[s] ?? 0) }));
    const base = categoryTable("Period", months.map((m) => (starred.has(m.month) ? `${m.month} (${newestTag})` : m.month)), bands);
    const span = t3.length ? `${t3[0].month} to ${t3[t3.length - 1].month}` : null;
    const table: ChartTable = {
      ...base,
      rows: span === null || subs.length === 0 ? base.rows
        : [...base.rows, [`3-month average, ${span}`, ...subs.map((s) => avg.get(s) ?? 0)]],
      csvName: "income-by-source.csv", label: "income by source",
      note: "A month with no income from a stream shows a zero for it."
        + (span === null ? "" : ` The last row is each stream's average month over the ${t3.length === 1 ? "newest full month"
          : `${t3.length} newest full months`}, whatever the window (a stream that began inside them is averaged from its first month).`),
    };
    return {
      streams: subs.length,
      table,
      option: {
        grid: baseGrid,
        legend: wrapLegend(),
        tooltip: moneyTooltip(),
        xAxis: monthAxis(months.map((m) => m.month), starred),
        yAxis: moneyAxis(fmtCompact),
        series: bands.map((b, i) => ({ ...b, type: "bar", stack: "income", color: streamColor(i) })),
      },
    };
  }, [months, streamAvg, t3, starred, newestTag]);

  const burn = useMemo(() => monthlyBurn(data.cashflow, inProgress), [data.cashflow, inProgress]);
  // two rated months or more in the window: the best and the worst are two months (savingsKpis sorts the rated months)
  const ranked = best !== null && worst !== null;
  // why there is none: fewer than two full months at all, fewer than two in the window, or full months of which fewer
  // than two had income to rate
  const unranked = t12.length < 2 ? NOT_ENOUGH_HISTORY
    : inWindow.length < 2 ? "fewer than two full months in this window"
    : "fewer than two months in this window with income to rate";
  // the month every trailing window ends on
  const through = lastFull ? ` through ${fmtMonth(lastFull)}` : "";
  const r3 = savingsRate(t3), r12 = savingsRate(t12);
  const gross12 = hasPayroll ? grossSavingsRate(t12, payrollPhases) : null;
  const monthLink = (ym: string) => {
    const [from, to] = monthBounds(ym);
    return spendingLink({ from, to });
  };
  // what a chart card says with no month to draw, in place of empty axes: no transaction at all (the long line, then the
  // short one), no month in coverage yet, or a window that holds none of the months there are
  const noMonth = (short: boolean) => (data.transactions.length === 0 ? <NoTransactions short={short} />
    : allMonths.length === 0 ? (short ? <EmptyState>No month in coverage yet.</EmptyState> : (
      <EmptyState>
        No month of cash flow is in coverage yet: a month counts once enough of the cash-flow accounts have their first
        transaction (<Code>coverage.min_share</Code> in <Code>assumptions.yaml</Code>).
      </EmptyState>
    ))
    : <EmptyState>No month of cash flow in this window. Pick a longer one.</EmptyState>);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-end gap-2">
        {/* an open end is the first month the page draws, or the month in progress, the last */}
        <DateRangeBar from={dr.range.from} to={dr.range.to} presets={PRESETS}
          openEnds={{ first: allMonths[0] ? `${allMonths[0].month}-01` : null, last: inProgress === null ? null : `${inProgress}-01` }}
          activePreset={dr.active(PRESETS)} onPreset={dr.setPreset}
          onChange={dr.set} granularity="month" unavailable={dr.maxDate === null ? NO_PRESET_REASON : undefined} />
      </div>
      {/* each trailing tile is labelled with the full months it really reads (3 and 12 once the history is that long);
          with no full month there is no window: "—" and not enough history yet. A null rate (no income in the window)
          reads "—" in the neutral tone, never green */}
      <div data-testid="kpis-cashflow" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {t3.length === 0 ? <StatCard label={savingsRateLabel(3)} value="—" sub={NO_FULL_MONTH} /> : (
          <StatCard label={savingsRateLabel(t3.length)} value={fmtPct(printableRate(r3))} tone={rateTone(printableRate(r3))}
            sub={`${belowRateFloor(r3) ? `${shortfallText(monthlyNet(t3))} · ` : ""}full months${through}`} />
        )}
        {/* the twelve-month rate over no more months than the three-month one is that same figure again: unknown until
            the history is longer (the total rate with payroll facts is another figure, and stays) */}
        {t12.length === 0 || (!hasPayroll && t12.length <= t3.length)
          ? <StatCard testId="kpi-savings-rate-12m" label={savingsRateLabel(12)} value="—"
              sub={t12.length === 0 ? NO_FULL_MONTH : NOT_ENOUGH_HISTORY} />
          : hasPayroll ? (
          <StatCard testId="kpi-savings-rate-12m" label={`Total savings rate (incl. retirement, ${t12.length} mo)`}
            value={fmtPct(printableRate(gross12))} tone={rateTone(printableRate(gross12))}
            sub={belowRateFloor(r12) ? `cash basis: ${shortfallText(monthlyNet(t12))}; 401(k)/match from payroll facts`
              : `cash basis ${fmtPct(r12)} + 401(k)/match from payroll facts`} />
        ) : (
          <StatCard testId="kpi-savings-rate-12m" label={savingsRateLabel(t12.length)} value={fmtPct(printableRate(r12))}
            sub={`${belowRateFloor(r12) ? `${shortfallText(monthlyNet(t12))} · ` : ""}full months${through} · cash basis`} />
        )}
        {/* the window's best and worst month: they need two rated full months in it, as one month is no comparison with
            itself. The label stays short, so it never wraps and drops its value a line below its neighbours'; the
            window is named on the line under the value */}
        <StatCard label="Best month" value={ranked ? fmtMonth(best.month) : "—"}
          sub={ranked ? `${savedText(best.savings_rate, best.income - best.spend)} · in this window` : unranked}
          href={ranked ? monthLink(best.month) : undefined} />
        <StatCard label="Worst month" value={ranked ? fmtMonth(worst.month) : "—"}
          sub={ranked ? `${savedText(worst.savings_rate, worst.income - worst.spend)} · in this window` : unranked}
          href={ranked ? monthLink(worst.month) : undefined} />
        <StatCard testId="kpi-monthly-burn" label="Monthly burn" value={burn === null ? "—" : fmtUSD0(burn)}
          sub={burn === null ? `${NOT_ENOUGH_HISTORY} — no full month of spending`
            : `avg monthly spend, trailing ${countOf(Math.min(6, t12.length), "full month")}${through}`}
          href="#/spending" />
      </div>
      <p className="-mt-2 text-[10px] text-faint">
        Savings rates see take-home pay only. Category budgets and payroll facts (the 401(k) and match
        behind the total savings rate) are on the{" "}
        <a href="#/cash-flow/budget" className="text-emerald-400 underline hover:text-emerald-300">Budget tab</a>.
      </p>

      <Card testId="card-cashflow-main" title={`Monthly income vs spending vs invested — ${words.click} a bar to drill into that month`}>
        {months.length === 0 ? noMonth(false) : <>
        <EChart option={main.option} tableData={main.table} height={400} onClick={drillToSpending} />
        {main.undrawn.length > 0 && (
          <p data-testid="cashflow-rate-undrawn" className="mt-1 text-[10px] text-faint">
            {main.undrawn.length === 1
              ? `Every month's ${main.undrawn[0]} here is below −50%, so the line is not drawn; each month's tooltip has its rate.`
              : `Every month's ${main.undrawn.join(" and ")} here are below −50%, so neither line is drawn; each month's `
                + "tooltip has both rates."}
          </p>
        )}
        {main.pinned && (
          <p data-testid="cashflow-rate-pinned" className="mt-1 text-[10px] text-faint">
            A month below −50% is drawn at the bottom of the chart; its tooltip has the rate.
          </p>
        )}
        <p className="mt-1 text-[10px] text-faint">
          {inProgress !== null && months.some((m) => m.month === inProgress) && (behind
            ? `* The data ends in ${fmtMonth(inProgress)}: later transactions have not been read yet, so no rate above counts that month. `
            : `* ${fmtMonth(inProgress)} is still in progress: its bills are still coming in, so no rate above counts it. `)}
          Shaded vertical bands mark employment phases detected from payroll deposits
          (one band per person and employer). Greyed months have incomplete account
          coverage — an account whose data begins later understates household income
          before that date. Transfers and credit-card payments are excluded throughout.
          {" "}{words.zoom}
        </p>
        </>}
      </Card>

      <Card testId="card-income-by-source" title="Income by source">
        {months.length === 0 ? noMonth(true) : income.streams === 0 ? (
          <EmptyState>No income in the months of this window.</EmptyState>
        ) : <>
        <EChart option={income.option} tableData={income.table} height={320} />
        <p className="mt-1 text-[10px] text-faint">
          One band per income stream (payroll and deposit detection; pre-tax 401(k) contributions never reach the bank).
          “View data” lists every stream by month{t3.length > 0 && <> and ends on each stream’s average month over the{" "}
          {countOf(t3.length, "full month")}{through}</>}.
        </p>
        </>}
      </Card>
    </div>
  );
}

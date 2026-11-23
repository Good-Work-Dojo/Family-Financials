// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Second-house chart cards: pure presentational blocks rendering the computed
// scenario math (cost stack, rental model, sensitivities, equity curve).
// State and derivation live in SecondHouse.tsx; math in lib/secondHouse.ts.
import { useMemo } from "react";
import EChart from "../components/EChart";
import { CurrencyInput, InputRow, NumberInput, SliderRow } from "../components/inputs";
import { Card } from "../components/ui";
import {
  baseGrid, CHART_GAIN, CHART_MUTED, CHART_STRONG, CHART_SURFACE, CHART_TEXT, moneyAxis, moneyTooltip, moneyValue,
} from "../lib/echarts";
import { fmtCompact, fmtUSD0, fmtUSDExact } from "../lib/format";
import type { SavingsPace } from "../lib/lifeSummary";
import {
  CarryBreakdown, downPaymentSweep, equityCurve, FundingRow, FundingState,
  rateSensitivity, RentalBreakdown, SecondHouseInputs,
} from "../lib/secondHouse";
import { NEGATIVE, NEUTRAL_MARK, WARNING } from "../lib/tokens";
import { useIsMobile } from "../lib/useMediaQuery";

const BLUE = "#3b82f6";
const SLATE = NEUTRAL_MARK;

/** what the page says of mortgage insurance under 20% down: only that it leaves its cost out (no figure here prices it,
 *  and nothing says what a lender does) — the mortgage-insurance tile and the down-payment sweep */
export const PMI_NOT_IN_PAYMENT = "Under 20% down a lender may require mortgage insurance; its cost is not in the payment shown.";

/** percent-number → text (fmtPct multiplies by 100, so it's wrong here);
 *  parseFloat trims trailing zeros: 6.5 @ dp=3 → "6.5%", not "6.500%" */
export const pctText = (v: number, dp = 1) => `${parseFloat(v.toFixed(dp))}%`;

export interface Ctx {
  /** today's liquid cash (null with no snapshot) and the essentials' $/mo (null with no transaction, or too few full
   *  months, to average) */
  liquidCash: number | null; essential: number | null;
  /** the household's average monthly free cash flow, income less spending (lib/lifeSummary savingsPace) and the full
   *  months it is averaged over; null with too little history: unknown, never 0, and nothing is set against it */
  freeCash: SavingsPace | null;
  /** why a run-rate is unknown: no transaction at all, or how many full months there are, and the day the next one ends
   *  (lib/agg.ts nextFullMonthEnds: from the exports; null when the data stops short of the ingest month) */
  noTransactions: boolean; fullMonths: number; nextFullMonthEnds: string | null;
  /** the current home's P&I (+ escrow when reported); null when the P&I is unknown, so no line is drawn */
  primaryHomeMonthly: number | null; ltcgPct: number; generatedAt: string;
  /** some account names a bank-feed id (manifest.setup.feed): only then do balances come from the feed's pulls */
  feed: boolean;
}

export interface Derived {
  carry: CarryBreakdown; rental: RentalBreakdown;
  needed: number; pi: number; netCost: number; breakeven: number | null;
  /** the funding table's rows and the plan's verdict (lib/secondHouse fundingState) */
  rows: FundingRow[]; funding: FundingState; cashDrawn: number;
}

/** The value axis's top when a line sits above every bar (`barsTop`): the least of 1, 1.5, 2.5 and 5 times a power of
 *  ten at or above the highest line, so the line is inside the plot and, on an axis from zero, the axis's own five-step
 *  ticks still land on its top (each of those over five is a step the axis picks itself). null when no line is above
 *  the bars: the axis's own top is then above every line already. */
function axisTopFor(barsTop: number, lines: number[]): number | null {
  const high = Math.max(0, ...lines);
  if (high <= barsTop) return null;
  const p = 10 ** Math.floor(Math.log10(high));
  return [1, 1.5, 2.5, 5, 10].map((m) => m * p).find((t) => t >= high)!;
}

/** The value axis from `lo` (below zero: a rental that earns more than the carry leaves a negative net) to a raised top
 *  `hi`, in ONE even step: the least 1, 2, 2.5 or 5 times a power of ten that spans both ends in at most `maxSteps`
 *  steps, the ends on that step, so zero is a tick and every tick is the same distance apart (a fixed top over an
 *  automatic bottom left ECharts' ticks uneven). */
export function evenSpan(lo: number, hi: number, maxSteps = 7): { min: number; max: number; interval: number } {
  let p = 10 ** Math.floor(Math.log10(Math.max(1, (hi - lo) / maxSteps)));
  for (;;) {
    for (const m of [1, 2, 2.5, 5]) {
      const s = m * p;
      const below = Math.ceil(-lo / s), above = Math.ceil(hi / s);
      if (below + above <= maxSteps) return { min: -below * s, max: above * s, interval: s };
    }
    p *= 10;
  }
}

/** a rate or a count in the arithmetic's words: as given, to at most two decimals ("12.96", "5") */
const exactNumber = (v: number): string => String(Math.round(v * 100) / 100);
/** an amount in the arithmetic's words (fmtUSDExact): whole dollars when it is whole, else to the cent, so a line's own
 *  factors multiply to what it shows ("3 × $33.50", never "3 × $34") */
const exactMoney = fmtUSDExact;

/** category-axis labels on a phone: eight bars share ~250px, and a label that overlaps its
 *  neighbour is hidden - which dropped the closing "Net /mo" bar's name */
const SHORT_CATS: Record<string, string> = {
  Insurance: "Ins.", Utilities: "Util.", Maintenance: "Maint.", "Net /mo": "Net",
};

export function CostBreakdownCard({ d, ctx }: { d: Derived; ctx: Ctx }) {
  const phone = useIsMobile();
  // the free-cash line is drawn only when that figure is known (and above zero)
  const freeCash = ctx.freeCash?.monthly ?? null;
  const option = useMemo(() => {
    const { carry, rental, netCost } = d;
    const costs: [string, number][] = [
      ["P&I", carry.pi], ["Tax", carry.tax], ["Insurance", carry.insurance],
      ["HOA", carry.hoa], ["Utilities", carry.utilities], ["Maintenance", carry.maintenance],
    ];
    const cats = [...costs.map(([n]) => n), "Rental", "Net /mo"];
    const offsets: number[] = [];
    const bars: { value: number; itemStyle: { color: string } }[] = [];
    let run = 0;
    for (const [idx, [, v]] of costs.entries()) {
      offsets.push(run);
      bars.push({ value: v, itemStyle: { color: idx === 0 ? BLUE : SLATE } });
      run += v;
    }
    // rental offsets the stack downward when it earns, adds on top when negative.
    // Its visible bar is clamped at the carry total (ECharts won't stack a
    // negative offset under a positive bar); when rental > carry the Net bar
    // below zero carries the "profit" signal and the label shows the full −net.
    if (rental.net >= 0) {
      const shown = Math.min(rental.net, run);
      offsets.push(run - shown);
      bars.push({ value: shown, itemStyle: { color: CHART_GAIN } });
    } else {
      offsets.push(run);
      bars.push({ value: -rental.net, itemStyle: { color: NEGATIVE } });
    }
    offsets.push(0);
    bars.push({ value: netCost, itemStyle: { color: CHART_STRONG } });
    const markData = [
      ...(ctx.primaryHomeMonthly !== null && ctx.primaryHomeMonthly > 0
        ? [{ name: "current\nhome", yAxis: Math.round(ctx.primaryHomeMonthly),
            lineStyle: { color: CHART_MUTED, type: "dashed" as const } }] : []),
      ...(freeCash !== null && freeCash > 0
        ? [{ name: "free\ncash", yAxis: Math.round(freeCash),
            lineStyle: { color: WARNING, type: "dashed" as const } }] : []),
    ];
    // the caption names every dashed line, so every one is on the chart: a markLine past the axis's top is clipped away,
    // so a line above the tallest bar (the carry stack, or the net when a loss-making rental adds to it) raises the top
    const top = axisTopFor(Math.max(run, netCost, 0), markData.map((m) => m.yAxis));
    return {
      // desktop: the two dashed lines name themselves just past the plot's right edge, in a gutter. On a phone
      // those end labels collided with the Net bar's value label, so the lines go unlabelled there (no gutter)
      // and the caption below names each one by its colour at every width
      grid: { ...baseGrid, top: 40, right: phone ? 16 : 44 },
      tooltip: {
        trigger: "item",
        formatter: (p: { dataIndex: number }) => {
          const name = cats[p.dataIndex];
          const v = name === "Rental" ? rental.net : bars[p.dataIndex].value;
          return `${name}: <b>${fmtUSD0(name === "Rental" ? -v : v)}</b>/mo`;
        },
      },
      xAxis: { type: "category", data: cats,
        axisLabel: { color: CHART_TEXT, interval: 0, fontSize: 10,
          ...(phone ? { formatter: (v: string) => SHORT_CATS[v] ?? v } : {}) } },
      // a raised top over an axis from zero keeps ECharts' own even steps; with a negative net below zero, one even step
      // spans both ends
      yAxis: { ...moneyAxis(fmtCompact),
        ...(top === null ? {} : netCost < 0 ? evenSpan(netCost, top) : { max: top }) },
      series: [
        { type: "bar", stack: "w", itemStyle: { color: "transparent" },
          emphasis: { disabled: true }, tooltip: { show: false }, silent: true,
          data: offsets.map((v) => Math.round(v)) },
        { type: "bar", stack: "w", barWidth: "55%",
          data: bars.map((b, k) => ({ ...b, value: Math.round(b.value),
            label: { show: true, position: (k === cats.length - 2 && d.rental.net >= 0
              ? "bottom" : "top") as "top" | "bottom",
              color: CHART_TEXT, fontSize: 10,
              formatter: () => (cats[k] === "Rental" && d.rental.net >= 0 ? "−" : "")
                + fmtCompact(Math.abs(cats[k] === "Rental" ? d.rental.net : b.value)) } })),
          markLine: markData.length ? {
            silent: true, symbol: "none",
            label: phone ? { show: false } : { color: CHART_TEXT, fontSize: 10, formatter: "{b}" },
            data: markData,
          } : undefined },
      ],
    };
  }, [d, ctx.primaryHomeMonthly, freeCash, phone]);
  // the caption names each dashed line that is drawn, by its colour (on a phone it is the only key)
  const lines = [
    ctx.primaryHomeMonthly !== null && ctx.primaryHomeMonthly > 0 ? "grey: what the current home costs per month" : null,
    freeCash !== null && freeCash > 0 ? "amber: the household's average monthly free cash flow" : null,
  ].filter(Boolean);
  return (
    <Card testId="card-cost-breakdown" title="Monthly cost breakdown">
      <EChart option={option} height={340} />
      <p className="mt-1 text-[10px] text-faint">
        Carrying costs stack left to right; rental income (green) pulls the total
        down to the net.{lines.length > 0 && ` Dashed lines — ${lines.join("; ")}.`}
      </p>
    </Card>
  );
}

export function RentalCard({ i, set, d }: {
  i: SecondHouseInputs; set: (p: Partial<SecondHouseInputs>) => void; d: Derived;
}) {
  const { rental, breakeven } = d;
  return (
    <Card testId="card-rental-model" title="Short-term rental model">
      <div className="grid gap-4 xl:grid-cols-3">
        <div className="flex flex-col">
          <InputRow label="Nightly rate">
            <CurrencyInput value={i.nightlyRate} onCommit={(v) => set({ nightlyRate: v })} className="w-28 sm:w-20" />
          </InputRow>
          <SliderRow label="Occupancy" sub={`≈ ${rental.bookedNights.toFixed(0)} nights/mo`}
            value={i.occupancyPct} onCommit={(v) => set({ occupancyPct: v })}
            min={0} max={100} step={1} unit="%" />
          <InputRow label="Nights available" sub="per month">
            <NumberInput value={i.nightsPerMonth} onCommit={(v) => set({ nightsPerMonth: v })}
              min={0} max={31} step={1} />
          </InputRow>
          <InputRow label="Platform fee" sub="Airbnb/VRBO, % of gross">
            <NumberInput value={i.platformFeePct} onCommit={(v) => set({ platformFeePct: v })}
              min={0} max={50} step={1} unit="%" />
          </InputRow>
          <InputRow label="Management fee" sub="% of gross">
            <NumberInput value={i.mgmtFeePct} onCommit={(v) => set({ mgmtFeePct: v })}
              min={0} max={50} step={1} unit="%" />
          </InputRow>
          <InputRow label="Cleaning" sub="per turnover">
            <CurrencyInput value={i.cleaningPerStay} onCommit={(v) => set({ cleaningPerStay: v })} className="w-28 sm:w-20" />
          </InputRow>
          <InputRow label="Stays" sub="per month">
            <NumberInput value={i.staysPerMonth} onCommit={(v) => set({ staysPerMonth: v })}
              min={0} max={31} step={1} />
          </InputRow>
        </div>
        <div className="text-xs">
          <table className="w-full">
            <tbody>
              <tr className="border-b border-zinc-800/60">
                {/* each line shows its own factors exactly, so they multiply to what it shows; the lines are rounded
                    for reading and the net below is the rounded sum of the unrounded lines (rounded once, at the
                    total), so the shown lines may differ from it by a dollar or two while the total is the true one */}
                <td className="py-1 text-zinc-400">
                  Gross ({exactNumber(rental.bookedNights)} nights × {exactMoney(i.nightlyRate)})
                </td>
                <td className="py-1 text-right tabular-nums text-zinc-200">{fmtUSD0(rental.gross)}</td>
              </tr>
              <tr className="border-b border-zinc-800/60">
                <td className="py-1 text-zinc-400">− Platform fee ({pctText(i.platformFeePct, 0)})</td>
                <td className="py-1 text-right tabular-nums text-zinc-400">−{fmtUSD0(rental.platformFee)}</td>
              </tr>
              <tr className="border-b border-zinc-800/60">
                <td className="py-1 text-zinc-400">− Management ({pctText(i.mgmtFeePct, 0)})</td>
                <td className="py-1 text-right tabular-nums text-zinc-400">−{fmtUSD0(rental.mgmtFee)}</td>
              </tr>
              <tr className="border-b border-zinc-800/60">
                <td className="py-1 text-zinc-400">− Cleaning ({i.staysPerMonth} × {exactMoney(i.cleaningPerStay)})</td>
                <td className="py-1 text-right tabular-nums text-zinc-400">−{fmtUSD0(rental.cleaning)}</td>
              </tr>
              <tr>
                <td className="py-1.5 font-semibold text-zinc-200">Net rental /mo</td>
                <td className={`py-1.5 text-right font-semibold tabular-nums ${
                  rental.net >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                  {fmtUSD0(rental.net)}
                </td>
              </tr>
            </tbody>
          </table>
          <p className="mt-2 text-[10px] text-faint">
            Occupancy and stays/month are independent knobs — sanity-check that
            {" "}{i.staysPerMonth} stays plausibly produce ≈{rental.bookedNights.toFixed(0)} booked nights.
            Income taxes on rental revenue aren't modeled.
          </p>
        </div>
        <BreakevenGauge occupancyPct={i.occupancyPct} breakeven={breakeven} carry={d.carry.total} />
      </div>
    </Card>
  );
}

function BreakevenGauge({ occupancyPct, breakeven, carry }: {
  occupancyPct: number; breakeven: number | null; carry: number;
}) {
  const achievable = breakeven !== null && breakeven <= 100;
  const option = useMemo(() => ({
    series: [{
      type: "gauge", min: 0, max: 100, splitNumber: 5, startAngle: 210, endAngle: -30,
      axisLine: { lineStyle: { width: 14, color: achievable
        ? [[breakeven! / 100, NEGATIVE], [1, CHART_GAIN]]
        : [[1, WARNING]] } },
      // a pointer shorter than the label ring and a readout below the open arc: neither can sit on
      // a tick label (the default pointer tip and the centred "45%" both did)
      pointer: { length: "34%", itemStyle: { color: CHART_STRONG } },
      axisTick: { show: false },
      splitLine: { length: 10, lineStyle: { color: CHART_SURFACE } },
      axisLabel: { color: CHART_TEXT, distance: 14, fontSize: 10 },
      detail: { color: CHART_STRONG, fontSize: 18, offsetCenter: [0, "60%"], formatter: (v: number) => `${v.toFixed(0)}%` },
      data: [{ value: occupancyPct }],
    }],
  }), [occupancyPct, breakeven, achievable]);
  return (
    <div>
      <EChart option={option} height={190} />
      <p className="text-center text-xs text-zinc-400">
        {achievable ? (
          <>breakeven at <b>{breakeven!.toFixed(0)}% occupancy</b> — above it, rental
            covers the full {fmtUSD0(carry)}/mo carry</>
        ) : (
          <span className="text-amber-400">
            breakeven not achievable at this nightly rate — rental can't cover the
            {" "}{fmtUSD0(carry)}/mo carry even at 100% occupancy
          </span>
        )}
      </p>
    </div>
  );
}

export function RateSensitivityCard({ i, pi }: { i: SecondHouseInputs; pi: number }) {
  const option = useMemo(() => {
    const lo = Math.min(4.5, i.ratePct);
    const hi = Math.max(8.5, i.ratePct);
    const pts = rateSensitivity(i, lo, hi);
    return {
      grid: baseGrid,
      tooltip: { trigger: "axis",
        valueFormatter: (v: number | null | undefined) => moneyValue(v),
        axisPointer: { label: { formatter: (p: { value: number }) => pctText(Number(p.value), 2) } } },
      // interval 1: a tick per whole percent (the auto interval put half-points a label's width apart, so
      // the labels touched)
      xAxis: { type: "value", min: lo, max: hi, interval: 1,
        axisLabel: { color: CHART_TEXT, formatter: (v: number) => `${v}%` },
        splitLine: { show: false } },
      yAxis: moneyAxis(fmtCompact),
      series: [{
        name: "Monthly P&I", type: "line", color: BLUE, showSymbol: false,
        data: pts.map((p) => [p.ratePct, Math.round(p.pi)]),
        markPoint: { symbolSize: 8, itemStyle: { color: WARNING },
          label: { show: false },
          data: [{ coord: [i.ratePct, Math.round(pi)] }] },
        markLine: { silent: true, symbol: "none",
          lineStyle: { color: WARNING, type: "dashed" },
          label: { color: CHART_TEXT, fontSize: 10, formatter: "your rate", position: "insideEndBottom" },
          data: [{ xAxis: i.ratePct }] },
      }],
    };
  }, [i, pi]);
  return (
    <Card testId="card-rate-sensitivity" title="Rate sensitivity">
      <EChart option={option} height={240} />
      <p className="mt-1 text-[10px] text-faint">
        Monthly P&I on this loan across rates — what a rate lock or a point buy-down is worth.
      </p>
    </Card>
  );
}

export function SweepCard({ i }: { i: SecondHouseInputs }) {
  const option = useMemo(() => {
    const pts = downPaymentSweep(i);
    return {
      grid: { ...baseGrid, right: 48 },
      legend: { textStyle: { color: CHART_TEXT, fontSize: 10 } },
      tooltip: moneyTooltip(),
      // the axis runs 2.5 past each end so the 10% and 50% bars sit fully inside the plot instead of half
      // outside it; only the multiples of 10 are labelled
      xAxis: { type: "value", min: 7.5, max: 52.5, interval: 2.5,
        axisLabel: { color: CHART_TEXT, formatter: (v: number) => (v % 10 === 0 ? `${v}%` : "") },
        splitLine: { show: false } },
      yAxis: [
        moneyAxis(fmtCompact),
        { ...moneyAxis(fmtCompact), splitLine: { show: false } },
      ],
      series: [
        // a solid neutral, not the muted grey at 0.6 (about 1.6:1 on the card in both themes)
        { name: "Cash to close", type: "bar", yAxisIndex: 1, barWidth: 10,
          itemStyle: { color: NEUTRAL_MARK },
          data: pts.map((p) => [p.downPct, Math.round(p.cashToClose)]) },
        { name: "Monthly P&I", type: "line", color: BLUE, showSymbol: false,
          data: pts.map((p) => [p.downPct, Math.round(p.pi)]),
          markArea: { silent: true,
            itemStyle: { color: NEGATIVE, opacity: 0.06 },
            // no label on the chart: it collides with the "yours" line and the axis, and the caption says what the band
            // is (the down payments under 20%) and what a lender may then require
            label: { show: false },
            data: [[{ name: "under 20% down", xAxis: 10 }, { xAxis: 20 }]] },
          ...(i.downPct >= 10 && i.downPct <= 50 ? {
            markLine: { silent: true, symbol: "none",
              lineStyle: { color: WARNING, type: "dashed" },
              // inside the plot's foot, under the legend's row rather than through it
              label: { color: CHART_TEXT, fontSize: 10, formatter: "yours", position: "insideEndBottom" },
              data: [{ xAxis: i.downPct }] },
          } : {}) },
      ],
    };
  }, [i]);
  return (
    <Card testId="card-down-payment-sweep" title="Down-payment sweep">
      <EChart option={option} height={240} />
      <p className="mt-1 text-[10px] text-faint">
        More down = lower payment but more cash locked in. Bars are cash to close (right axis); the shaded band is under
        20% down. {PMI_NOT_IN_PAYMENT}
      </p>
    </Card>
  );
}

export function EquityCurveCard({ i }: { i: SecondHouseInputs }) {
  const option = useMemo(() => {
    const curve = equityCurve(i);
    // yearly samples, plus the payoff month when it isn't on a year boundary
    const pts = curve.filter((p, k) =>
      p.month % 12 === 0 || k === curve.length - 1);
    return {
      grid: baseGrid,
      legend: { textStyle: { color: CHART_TEXT, fontSize: 10 } },
      tooltip: moneyTooltip(),
      xAxis: { type: "category", data: pts.map((p) => `${Math.round(p.month / 12)}yr`),
        axisLabel: { color: CHART_TEXT } },
      yAxis: moneyAxis(fmtCompact),
      series: [
        { name: "Home equity", type: "line", color: CHART_GAIN, showSymbol: false,
          areaStyle: { opacity: 0.12 }, data: pts.map((p) => Math.round(p.equity)) },
        { name: "Loan balance", type: "line", color: SLATE, showSymbol: false,
          data: pts.map((p) => Math.round(p.balance)) },
      ],
    };
  }, [i]);
  return (
    <Card testId="card-equity-curve" title="Equity over 30 years">
      <EChart option={option} height={240} />
      <p className="mt-1 text-[10px] text-faint">
        Home value held flat at the purchase price — appreciation deliberately not assumed.
      </p>
    </Card>
  );
}

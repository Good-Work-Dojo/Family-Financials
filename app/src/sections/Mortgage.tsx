// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo, useState } from "react";
import AssumptionsPanel, { AssumptionField } from "../components/AssumptionsPanel";
import DefaultTag from "../components/DefaultTag";
import EChart from "../components/EChart";
import { NoHomeValue } from "../components/ManualAssetsLink";
import MissingLoanTerms from "../components/MissingLoanTerms";
import { Code, EmptyState } from "../components/NoData";
import { AsOfBadge, Card, StatCard } from "../components/ui";
import { useData } from "../data/DataContext";
import { useSnapshots } from "../data/useSnapshots";
import { currentYear } from "../lib/agg";
import { baseGrid, baseLegend, CHART_STRONG, CHART_TEXT, moneyAxis, moneyTooltip } from "../lib/echarts";
import { fmtCompact, fmtDate, fmtDelta, fmtMonth, fmtPct, fmtRate, fmtUSD0, fmtUSDExact } from "../lib/format";
import { balanceHistory, fmtLoanToValue, homeValue, homeValueSlider, loanToValue, missingLoanTerms,
  mortgageBalance } from "../lib/lifeSummary";
import { amortize, loanEffectivePct, projectGrowth } from "../lib/projections";
import { useGrowthSeed, useTouched } from "../lib/useGrowthSeed";

/** why there is no projection when the monthly P&I is known and does not cover a month's interest: the words the error
 *  box, the footnote and the tiles beside it share */
const PAYMENT_TOO_SMALL = "the monthly P&I does not pay the loan down";

/** ISO date -> fractional year (day-of-month over 31: close enough for an x axis) */
const yearFraction = (iso: string) =>
  Number(iso.slice(0, 4)) + (Number(iso.slice(5, 7)) - 1 + Number(iso.slice(8, 10)) / 31) / 12;

/** "a", "a and b", "a, b and c" */
const andList = (xs: string[]) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

/** the slider values with the what-if home value set to `home`, or left out when there is no home value */
function withHome(vals: Record<string, number>, home: number | null): Record<string, number> {
  const { homeValue: _home, ...rest } = vals;
  return home === null ? rest : { ...rest, homeValue: home };
}

export default function Mortgage() {
  const { data } = useData();
  const mg = data.assumptions.mortgage;
  const hh = data.assumptions.household;
  // the one mortgage balance (lib/lifeSummary mortgageBalance), the bank feed's, as on Accounts -> Liabilities and
  // Balances. null before the feed reports it — render "—", never a literal
  const mb = mortgageBalance(data.assumptions);
  const balance = mb?.balance ?? null;
  const paidOff = balance === 0;
  const account = mg ? data.accounts.find((a) => a.id === mg.account_id) : undefined;
  // the home's value is the current snapshot's House-class rows (lib/lifeSummary homeValue); null without one, and then
  // there is no home-value slider and equity and loan-to-value read "—"
  const current = useSnapshots().latest;
  const home = current ? homeValue(current) : null;
  // the rate and the payment are the loan terms (accounts.yaml `loan:`), the payment as stated (to the cent: a payment
  // rounded to the dollar would move the payoff); unknown is null, and its slider starts at 0, its box empty
  const defaults: Record<string, number> = {
    ...(home !== null ? { homeValue: home } : {}),
    apr: mg?.rate_pct ?? 0,
    monthly: mg?.monthly_payment ?? 0,
    extra: 0,
    lump: 0,
  };
  // the slider values are kept with the home value they were set against: when a reload brings another home value, the
  // what-if home value starts again at it (inside its new range) and the other sliders keep what was set
  const [kept, setKept] = useState({ vals: { ...defaults }, home });
  const vals = useMemo(() => (kept.home === home ? kept.vals : withHome(kept.vals, home)), [kept, home]);
  const setVals = (v: Record<string, number>) => setKept({ vals: v, home });
  // a term is known when the loan terms state it — a rate of 0 is a rate (an interest-free loan) — or the person has set
  // it here (the slider moved or a value typed, 0 included, so a box keeps what is typed in it); an unknown term feeds no
  // figure, and the page names it. Emptying an unstated term's box (the rate's or the payment's), or "reset", makes it
  // unknown again
  const { touched, touch, untouch, forget } = useTouched();
  const rateKnown = mg?.rate_pct != null || touched.has("apr");
  const payKnown = mg?.monthly_payment != null || touched.has("monthly");
  // what the projection lacks, in words, and the sentences that say which loan term to add (the escrow is no input to it)
  const lacks = [balance === null ? "mortgage balance" : null, rateKnown ? null : "rate", payKnown ? null : "monthly P&I"]
    .filter((x): x is string => x !== null);
  const needs = lacks.length ? `the ${andList(lacks)}` : null;
  const missingTerms = mg === null ? [] : missingLoanTerms({
    ...mg, rate_pct: rateKnown ? vals.apr : null, monthly_payment: payKnown ? vals.monthly : null,
  }).filter((t) => t.key !== "monthly_escrow");
  // the projection starts where the balance is dated, not at a hardcoded year fraction
  const asOf = mb?.asOf ?? null;
  const startYear = asOf ? yearFraction(asOf) : currentYear(data.manifest);
  // escrow figures print only when the loan terms state a positive one: never "$0"
  const escrowMonthly = mg?.monthly_escrow != null && mg.monthly_escrow > 0 ? mg.monthly_escrow : null;
  const escrowCushion = mg?.escrow_balance != null && mg.escrow_balance > 0 ? mg.escrow_balance : null;
  const hist = useMemo(() => balanceHistory(mg?.schedule), [mg?.schedule]);
  const terms = `from your loan terms${account ? ` (${account.name})` : ""}`;
  // the margin comparison needs the margin account's rate (assumptions.yaml household.margin_apr); without one it is
  // left out and the footnote says what to add, never a guessed rate
  const margin = hh.margin_account;
  const marginApr = hh.margin_apr;

  const fields: AssumptionField[] = [
    // the slider runs from half the home's value to twice it (homeValueSlider)
    ...(home === null ? [] : [{ key: "homeValue", label: "Home value", ...homeValueSlider(home), unit: "$",
      source: "the home as entered by hand, in Settings → Manual assets (the House class) — slide for a what-if" }]),
    // a stated rate under 2% (an interest-free loan's 0 too) is inside the slider's range
    // a rate the loan terms leave out: emptying its box makes it unstated again, as "reset" does
    { key: "apr", label: "Rate (APR)", min: mg?.rate_pct != null ? Math.min(2, mg.rate_pct) : 2, max: 10, step: 0.005,
      unit: "%", clearable: mg?.rate_pct == null,
      source: mg?.rate_pct != null ? terms
        : "no rate in your loan terms — state it in the mortgage account's loan terms in the configuration (rate under "
          + "loan:), or set it here" },
    { key: "monthly", label: "Monthly P&I", min: 500, max: 6000, step: 50, unit: "$",
      source: mg?.monthly_payment != null
        ? terms + (escrowMonthly !== null ? ` — escrow ${fmtUSD0(escrowMonthly)}/mo is separate` : "")
        : "no monthly payment in your loan terms — state it in the mortgage account's loan terms in the configuration "
          + "(monthly_payment under loan:), or set it here" },
    { key: "extra", label: "Extra monthly", min: 0, max: 2000, step: 50, unit: "$" },
    { key: "lump", label: "One-time lump (now)", min: 0, max: 100000, step: 1000, unit: "$" },
  ];

  const result = useMemo(() => {
    // a known payment that does not pay the loan down (0, or no more than a month's interest) is amortize's error
    if (balance === null || balance <= 0 || !rateKnown || !payKnown) return { base: null, what: null, error: null };
    try {
      const base = amortize({ balance, annualRatePct: vals.apr, monthlyPayment: vals.monthly });
      const what = amortize({
        balance, annualRatePct: vals.apr, monthlyPayment: vals.monthly,
        extraMonthly: vals.extra, lumpSum: vals.lump > 0 ? { month: 1, amount: vals.lump } : undefined,
      });
      return { base, what, error: null };
    } catch (e) {
      return { base: null, what: null, error: String(e) };
    }
  }, [balance, vals, rateKnown, payKnown]);

  // an extra payment is set: the extra-payments projection differs from the current pace only then
  const extraSet = vals.extra > 0 || vals.lump > 0;
  // The chart draws what there is, and its legend lists exactly that: the history while the feed has reported points,
  // the current pace whenever there is a projection, and the extra-payments line only beside an extra payment (with
  // none it is the current pace again, drawn over it). Nothing to draw (no history, no projection): no chart at all.
  const chartOption = useMemo(() => {
    const base = result.base;
    if (!base && hist.length === 0) return null;
    // a projection's line starts at the balance it projects from, on that balance's date (the history's last point), so
    // the two meet with no gap; then every sixth month and the payoff
    const toPts = (sched: { month: number; balance: number }[]) => [
      ...(balance === null ? [] : [[startYear, Math.round(balance)]]),
      ...sched.filter((s) => s.month % 6 === 0 || s.balance === 0)
        .map((s) => [startYear + s.month / 12, Math.round(s.balance)]),
    ];
    const histYears = hist.map((r) => yearFraction(r.date));
    return {
      grid: baseGrid,
      legend: baseLegend(),
      tooltip: moneyTooltip(),
      xAxis: { type: "value", min: Math.min(startYear, ...histYears) - 0.1,
        max: base ? startYear + base.payoffMonths / 12 + 1 : Math.max(startYear, ...histYears) + 0.1,
        axisLabel: { color: CHART_TEXT, formatter: (v: number) => String(Math.floor(v)) },
        splitLine: { show: false } },
      yAxis: moneyAxis(fmtCompact),
      series: [
        ...(hist.length ? [{ name: "History (bank feed)", type: "scatter", color: CHART_STRONG, symbolSize: 6,
          data: hist.map((r) => [yearFraction(r.date), r.principal_balance]) }] : []),
        ...(base ? [{ name: "Current pace", type: "line", color: "#64748b", showSymbol: false,
          data: toPts(base.schedule) }] : []),
        ...(result.what && extraSet ? [{ name: "With extra payments", type: "line", color: "#22c55e", showSymbol: false,
          data: toPts(result.what.schedule) }] : []),
      ],
    };
  }, [result, hist, startYear, extraSet, balance]);

  // the what-if home value: the slider's, which starts at the snapshot's (an older state without one reads the snapshot's)
  const whatIfHome = home === null ? null : vals.homeValue ?? home;
  const equity = balance === null || whatIfHome === null ? null : whatIfHome - balance;
  const saved = result.base && result.what
    ? result.base.totalInterest - result.what.totalInterest : 0;
  const fasterMonths = result.base && result.what
    ? result.base.payoffMonths - result.what.payoffMonths : 0;
  const payoffYear = (m: number) => String(Math.floor(startYear + m / 12));
  // the page shows only for a household with a mortgage account (App.tsx PAGES `needs: "mortgage"`), so a missing
  // balance is the feed's not having reported it yet
  const balanceSub = mb === null ? "no balance from the bank feed yet"
    : `${paidOff ? "paid off · " : ""}from the bank feed${mb.asOf ? ` · ${fmtDate(mb.asOf)}` : ""}`;
  // why there is no projection, in the words every hint beside it uses: a missing term, or a payment the error box says
  // does not pay the loan down (no hint beside that error says anything else)
  const noProjection = needs !== null ? `needs ${needs}` : result.error ? PAYMENT_TOO_SMALL : null;

  return (
    <div className="space-y-4">
      {/* the balance's date; a balance the feed has not dated (or none at all) has no badge: the tile says where it is from */}
      {asOf !== null && (
        <div className="flex items-center justify-end">
          <AsOfBadge date={asOf} generatedAt={data.manifest.generated_at} />
        </div>
      )}

      <div data-testid="kpis-mortgage" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Principal balance" value={balance === null ? "—" : fmtUSD0(balance)} tone="neg"
          sub={balanceSub} />
        <StatCard label="Home equity" value={equity === null ? "—" : fmtUSD0(equity)} tone="pos"
          sub={balance === null
            ? "no mortgage balance in the data"
            : whatIfHome === null
              ? "no home in the current snapshot · LTV —"
              : `at ${fmtUSD0(whatIfHome)} home value · LTV ${fmtLoanToValue(loanToValue(balance, whatIfHome))}`} />
        <StatCard label="Payoff at current pace"
          value={paidOff ? "Paid off" : result.base ? payoffYear(result.base.payoffMonths) : "—"}
          sub={paidOff ? "the bank feed shows nothing owed"
            : result.base ? `${Math.floor(result.base.payoffMonths / 12)}y ${result.base.payoffMonths % 12}m · ${fmtUSD0(result.base.totalInterest)} total interest`
            : noProjection ?? undefined} />
        <StatCard label="With extra payments"
          value={result.what && extraSet ? payoffYear(result.what.payoffMonths) : "—"}
          tone={fasterMonths > 0 ? "pos" : undefined}
          sub={paidOff ? "nothing left to pay"
            : noProjection !== null ? noProjection
            : !extraSet ? "set an extra monthly payment or a lump below"
            : fasterMonths > 0
              ? `${Math.floor(fasterMonths / 12)}y ${fasterMonths % 12}m sooner · saves ${fmtUSD0(saved)} interest`
              : `the same payoff month · saves ${fmtUSD0(saved)} interest`} />
      </div>

      {home === null && <NoHomeValue className="text-[11px] text-zinc-500" />}
      {!paidOff && <MissingLoanTerms terms={missingTerms} />}
      {/* a term nobody stated has an empty box (its slider at its minimum) until the person sets it */}
      <AssumptionsPanel testId="card-mortgage-assumptions" fields={fields} values={vals} defaults={defaults} onChange={setVals}
        unset={[...(rateKnown ? [] : ["apr"]), ...(payKnown ? [] : ["monthly"])]} onSet={touch} onClear={untouch}
        onReset={forget} />
      {result.error && balance !== null && (
        <p data-testid="mortgage-payment-error" className="rounded border border-red-800 bg-red-950/40 p-2 text-xs text-red-300">
          The monthly P&amp;I of {fmtUSDExact(vals.monthly)} does not pay the loan down: a month&apos;s interest at {vals.apr}% on
          {" "}{fmtUSD0(balance)} is {fmtUSD0(balance * vals.apr / 100 / 12)}.
        </p>
      )}

      <Card testId="card-mortgage-trajectory" title="Balance trajectory — history + projection">
        {chartOption !== null && <EChart option={chartOption} height={340} />}
        <p className="mt-1 text-[10px] text-faint">
          {hist.length
            ? `History: the loan account's balance from the bank feed, ${fmtMonth(hist[0].date.slice(0, 7))} → ${fmtMonth(hist[hist.length - 1].date.slice(0, 7))}.`
            : "No balance history from the bank feed yet."}{" "}
          {mb && !paidOff && `The projection starts from the bank feed's balance${mb.asOf ? ` of ${fmtDate(mb.asOf)}` : ""}. `}
          {paidOff
            ? "No projection: the loan is paid off"
            : result.base
              // the payment as the projection uses it, cents included when it has them
              ? `Projection assumes monthly P&I of ${fmtUSDExact(vals.monthly)} at ${vals.apr}%`
              : needs !== null ? `No projection yet: it needs ${needs}`
              : result.error ? `No projection: ${PAYMENT_TOO_SMALL}`
              : "No projection yet"}
          {!paidOff && escrowMonthly !== null && ` — escrow (${fmtUSD0(escrowMonthly * 12)}/yr taxes + insurance${
            escrowCushion !== null ? `, ${fmtUSD0(escrowCushion)} escrow balance` : ""
          }) is separate and continues after payoff`}
          {". "}
          {/* the loan's own rate and what a prepaid dollar saves at it, beside the margin loan's stated rate when there is
              one: two rates the data states, set side by side, and no comparison with a market instrument nothing here
              prices */}
          {!paidOff && vals.apr > 0 && `At ${vals.apr}%, each dollar prepaid saves ${vals.apr}% a year in interest${
            margin && marginApr !== null ? `; a dollar paid off the ${margin} margin saves ${fmtRate(marginApr)}` : ""
          }.`}
          {!paidOff && vals.apr > 0 && margin && marginApr === null && <>
            {` To compare paying down the ${margin} margin, state that loan's yearly rate (`}<Code>household.margin_apr</Code>
            {" in "}<Code>assumptions.yaml</Code>{")."}
          </>}
        </p>
      </Card>

      {result.base ? (
        <OpportunityCostCard ratePct={vals.apr} horizonYears={result.base.payoffMonths / 12} pageExtra={vals.extra} />
      ) : (
        <div data-testid="card-prepay-vs-invest">
          <p data-testid="no-prepay-compare" className="text-[10px] text-zinc-500">
            {paidOff ? "The loan is paid off: there is nothing left to prepay."
              : needs !== null ? `The prepay-vs-invest comparison needs ${needs}.`
              : "The prepay-vs-invest comparison needs a monthly P&I that pays the loan down."}
          </p>
        </div>
      )}
    </div>
  );
}

/** Prepay vs invest: the same extra amount a month put against the loan at its own rate, or invested at the rate the
 *  person sets in this card, over the loan's remaining term, and the difference between the two in neutral words. No
 *  figure is a market's: the loan's rate is its terms', and the other rate is the card's own control, which starts at the
 *  middle of the household's return scenarios (lib/useGrowthSeed: "default" while those are unstated and the slider
 *  untouched). The amount is the page's Extra monthly while that is set and the card's own box is not; with neither,
 *  nobody stated one, and every figure reads "—". Emptying the card's box hands it back to the page's. */
function OpportunityCostCard({ ratePct, horizonYears, pageExtra }: {
  ratePct: number; horizonYears: number;
  /** the page's own "Extra monthly": the card's amount follows it while it is set and the card's box is not */
  pageExtra: number;
}) {
  const growth = useGrowthSeed();
  const defaults = { extra: 0, ret: growth.pct };
  const [vals, setVals] = useState<Record<string, number>>({ ...defaults });
  // the person set this card's own amount (its box or its slider); until then it is the page's extra monthly, if any
  const [own, setOwn] = useState(false);
  const { touched, touch, forget } = useTouched();
  const extra = own ? vals.extra : pageExtra > 0 ? pageExtra : null;
  const shown = extra !== null;
  const values: Record<string, number> = { ...vals, extra: extra ?? 0 };
  const retDefault = growth.isDefault && !touched.has("ret");
  const tag = retDefault ? <DefaultTag toData title={growth.title} /> : undefined;
  const fields: AssumptionField[] = [
    { key: "extra", label: "Extra $/month", min: 0, max: 3000, step: 100, unit: "$",
      source: "follows the Extra monthly above the chart until you set this one" },
    { key: "ret", label: "Return if invested", min: 0, max: 12, step: 0.5, unit: "%",
      source: "yours to set: it starts at the middle of the household's return scenarios", tag },
  ];
  const years = Math.round(horizonYears);
  // both sides on one footing, each as it grows: a prepaid dollar stops the loan's interest, which accrues a twelfth of
  // the APR each month (so a year's is the effective rate, loanEffectivePct); the invested one grows at the yearly rate set
  const loanYear = loanEffectivePct(ratePct);
  const fv = (r: number) => projectGrowth({
    principal: 0, monthlyContribution: extra ?? 0, annualReturnPct: r, years,
  }).slice(-1)[0].balance;
  const prepay = fv(loanYear);
  const invest = fv(values.ret);
  const diff = invest - prepay;
  const ends = Math.abs(diff) < 0.5 ? "at these rates, investing and prepaying end the same"
    : `at these rates, investing ends ${fmtUSD0(Math.abs(diff))} ${diff > 0 ? "higher" : "lower"} than prepaying`;
  return (
    <Card testId="card-prepay-vs-invest" title="Prepay vs invest — the same money at two rates">
      <AssumptionsPanel fields={fields} values={values} defaults={defaults} onChange={setVals}
        unset={shown ? [] : ["extra"]} onSet={(k) => { touch(k); if (k === "extra") setOwn(true); }}
        onClear={(k) => { if (k === "extra") { setOwn(false); setVals((v) => ({ ...v, extra: 0 })); } }}
        onReset={() => { forget(); setOwn(false); }} />
      {/* the three figures in whole dollars, the difference signed */}
      <div data-testid="prepay-tiles" className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <StatCard label={`Prepaying @ ${ratePct}%`} value={shown ? fmtUSD0(prepay) : "—"}
          sub={shown ? `${fmtUSD0(extra)}/mo for ~${years} yrs at the loan's rate, accrued monthly` : undefined} />
        <StatCard label={`Investing @ ${values.ret}%`} value={shown ? fmtUSD0(invest) : "—"} tag={shown ? tag : undefined}
          sub={shown ? `the same ${fmtUSD0(extra)}/mo at the rate set in this card` : undefined} />
        <StatCard label="Investing − prepaying" value={shown ? fmtDelta(diff) : "—"} sub={shown ? ends : undefined} />
      </div>
      {!shown && (
        <div className="mt-2">
          <EmptyState>
            Set this card&apos;s Extra $/month, or the Extra monthly above the chart, to compare prepaying that amount with
            investing it.
          </EmptyState>
        </div>
      )}
      <p data-testid="prepay-basis" className="mt-2 text-[10px] text-faint">
        Prepaying is worked out as the loan charges interest: {ratePct}% a year accrued monthly, {fmtPct(loanYear / 100, 2)}
        {" "}over a year, which each dollar prepaid stops. Investing grows at the rate set in this card, {values.ret}% a
        year, which nothing in your data states. Both are before tax on either side, and neither is a forecast. Money
        prepaid comes back only through a sale or a new loan; money invested can be withdrawn.
      </p>
    </Card>
  );
}

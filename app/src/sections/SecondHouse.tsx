// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Second-house scenario worksheet: up to 5 saved scenarios (deal terms,
// carrying costs, short-term-rental model, funding plan against live account
// balances). Math in lib/secondHouse.ts; persistence in data/useScenarioStore.
import { useMemo, useState } from "react";
import AccountLink from "../components/AccountLink";
import { CurrencyInput, InputRow, NumberInput, SliderRow } from "../components/inputs";
import { EmptyState as EmptyLine, historySoFar, NO_BALANCES_YET, NoBalances, NO_TRANSACTIONS_YET, NoTransactions,
  RUN_RATE_NEEDS } from "../components/NoData";
import RequireData from "../components/RequireData";
import { AsOfBadge, Card, Chip, StatCard, TableScroll, TAP } from "../components/ui";
import { useData } from "../data/DataContext";
import { useSnapshots } from "../data/useSnapshots";
import { ScenarioStoreApi, useScenarioStore } from "../data/useScenarioStore";
import { dataFirstDay, monthInProgress, nextFullMonthEnds, runRateMonths } from "../lib/agg";
import { useEfundOverrides } from "../lib/efundOverrides";
import { emergencyWindow } from "../lib/emergency";
import { countOf, fmtMonths, fmtUSD0 } from "../lib/format";
import { cashRunway, primaryHomeMonthly, savingsPace } from "../lib/lifeSummary";
import { MAX_SCENARIOS, Scenario } from "../lib/scenarioStore";
import {
  breakevenOccupancyPct, capGainsDragMax, carryBreakdown, cashLikeDrawTotal,
  amountText, belowZero, cashNeeded, comparisonRows, downPaymentUSD, fundingCloses, fundingRows, fundingState,
  type FundingVerdict,
  fundingVerdictBrief, fundingVerdictText, monthlyPI, netMonthlyCost, pmiLikely, rentalBreakdown, SecondHouseInputs,
} from "../lib/secondHouse";
import { useVocab } from "../lib/useVocab";
import {
  CostBreakdownCard, Ctx, Derived, EquityCurveCard, pctText, PMI_NOT_IN_PAYMENT,
  RateSensitivityCard, RentalCard, SweepCard,
} from "./secondhouse-cards";

export default function SecondHouse() {
  return (
    <RequireData files={["accounts.json", "cashflow_monthly.json"]} what="The second-house worksheet">
      <SecondHousePage />
    </RequireData>
  );
}

function SecondHousePage() {
  const { data } = useData();
  const api = useScenarioStore();
  // this device's essential toggles (Planning → Projections gauge): the cushion divides by the same
  // essentials the runway KPI and the gauge do
  const efund = useEfundOverrides();
  const vocab = useVocab();

  // the newest snapshot; null for a household with none yet (no cash figure, and the cash tiles say so)
  const cur = useSnapshots().latest;
  // household context, all from existing exports (never hardcoded)
  const ctx = useMemo((): Ctx => {
    // the cash and essentials Planning's runway KPI divides (lib/lifeSummary cashRunway), so the cushion cannot disagree
    // with it; each null when its data is missing (no snapshot; no transaction, or too few full months to average). Both,
    // and the free cash below (the savings pace, lib/lifeSummary savingsPace), read the full months before the month in
    // progress (lib/agg.ts monthInProgress), and need agg.ts RUN_RATE_MIN_MONTHS of them (runRateMonths: a first month
    // the data covers in part is none)
    const inProgress = monthInProgress(data);
    const firstDay = dataFirstDay(data.transactions);
    const { cash: liquidCash, essential } = cashRunway(vocab, data.transactions, cur,
      emergencyWindow(data.cashflow, inProgress, firstDay), efund);
    return {
      liquidCash, essential, freeCash: savingsPace(data.cashflow, inProgress, firstDay),
      noTransactions: data.transactions.length === 0,
      fullMonths: runRateMonths(data.cashflow, inProgress, firstDay).length, nextFullMonthEnds: nextFullMonthEnds(data),
      // the same P&I (+ escrow) rule the mortgage summary uses; null, not 0, when it is unknown
      primaryHomeMonthly: primaryHomeMonthly(data.assumptions),
      ltcgPct: data.assumptions.projections.ltcg_pct,
      generatedAt: data.manifest.generated_at,
      feed: data.manifest.setup.feed,
    };
  }, [data, cur, vocab, efund]);

  // each saved scenario's funding verdict, so a tab says which scenarios do not close
  const verdicts = useMemo(() => new Map(api.store.scenarios.map((s) =>
    [s.id, fundingState(fundingRows(data.accounts, s.draws), cashNeeded(s.inputs)).verdict])),
  [api.store.scenarios, data.accounts]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <SaveBadge api={api} />
      </div>
      {api.loading ? (
        // a status region, busy while the store answers: announced once, and what a wait for the page can watch
        <div role="status" aria-busy="true" className="animate-pulse text-zinc-500">Loading scenarios…</div>
      ) : (
        <>
          <ScenarioTabs api={api} verdicts={verdicts} />
          {api.active ? (
            <Worksheet key={api.active.id} scenario={api.active} api={api} ctx={ctx} />
          ) : (
            <EmptyState onCreate={api.create} />
          )}
          {api.store.scenarios.length >= 2 && (
            <ComparisonCard scenarios={api.store.scenarios} activeId={api.activeId}
              liquidCash={ctx.liquidCash} />
          )}
          {api.active && (
            <p className="text-[10px] text-faint">
              A deterministic worksheet, not a forecast: home value is held flat (no
              appreciation assumed), rental income is your own estimate, and the long-term capital gains (LTCG)
              note is an upper bound (it assumes the whole draw is gain).{" "}
              {ctx.feed
                ? "Account balances come from the bank feed — each shows its as-of date."
                : "Account balances are each account's latest — each shows its as-of date."}{" "}
              PMI, rate points, and purchase-price negotiation aren't modeled.
            </p>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- chrome

function SaveBadge({ api }: { api: ScenarioStoreApi }) {
  if (api.loading) return null;
  if (api.localOnly) {
    return (
      <span className="inline-flex items-center rounded-full bg-amber-900/50 px-2 py-0.5 text-[10px] text-amber-300"
        title="The dashboard's store is unreachable — edits persist in this browser only and sync once it answers again">
        local only
      </span>
    );
  }
  if (api.saveState === "saving") return <span className="text-[10px] text-zinc-500">saving…</span>;
  if (api.saveState === "saved") return <span className="text-[10px] text-emerald-500">saved ✓</span>;
  return null;
}

/** the mark's word for each verdict that does not close */
const DOES_NOT_CLOSE: Partial<Record<FundingVerdict, string>> = {
  overdraw: "overdraws", uncovered: "balance unknown", short: "short",
};

/** What marks a scenario that does not close, on its tab and in the comparison's header: a draw overdraws an account, a
 *  draw rests on a balance nobody knows, or the draws fall short of the cash needed (lib/secondHouse fundingState), in
 *  the tone the funding card gives that verdict; nothing for one that closes or can draw nothing. The words are part of
 *  the name a screen reader hears. */
function DoesNotClose({ verdict }: { verdict: FundingVerdict | undefined }) {
  if (verdict === undefined || verdict === "unknown" || fundingCloses(verdict) !== false) return null;
  return (
    <span className={`ml-1 whitespace-nowrap text-[10px] ${VERDICT_TONE[verdict].text}`}>⚠ {DOES_NOT_CLOSE[verdict]}</span>
  );
}

function ScenarioTabs({ api, verdicts }: {
  api: ScenarioStoreApi;
  /** each scenario's funding verdict, by id */
  verdicts: ReadonlyMap<string, FundingVerdict>;
}) {
  const [renaming, setRenaming] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const atCap = api.store.scenarios.length >= MAX_SCENARIOS;
  if (api.store.scenarios.length === 0) return null;
  return (
    <div data-testid="card-scenario-tabs" role="tablist" className="flex flex-wrap items-center gap-1">
      {api.store.scenarios.map((s) => {
        const active = s.id === api.activeId;
        return (
          <div key={s.id} role="tab" aria-selected={active}
            className={`flex items-center gap-1 rounded-lg border px-1 py-1 text-sm ${
              active ? "border-zinc-600 bg-zinc-800 text-zinc-100"
                : "border-transparent text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"}`}>
            {renaming === s.id ? (
              <input autoFocus defaultValue={s.name}
                className="w-32 rounded border border-zinc-600 bg-zinc-900 px-1 py-0.5 text-base sm:text-sm"
                onBlur={(e) => { api.rename(s.id, e.target.value); setRenaming(null); }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                  if (e.key === "Escape") setRenaming(null);
                }} />
            ) : (
              <button className={`px-2 py-0.5 ${TAP}`} onClick={() => api.setActiveId(s.id)}>
                {s.name}<DoesNotClose verdict={verdicts.get(s.id)} />
              </button>
            )}
            {active && renaming !== s.id && (
              confirming === s.id ? (
                <span className="flex items-center gap-1 pr-1 text-xs text-zinc-400">
                  delete?
                  {/* each glyph button is named in words (a finger never sees its title) and is a 44px target on a phone */}
                  <button title="confirm delete" aria-label={`Delete ${s.name}`} className={`text-red-400 hover:text-red-300 ${TAP}`}
                    onClick={() => { api.remove(s.id); setConfirming(null); }}>✓</button>
                  <button title="keep it" aria-label={`Keep ${s.name}`} className={`hover:text-zinc-200 ${TAP}`}
                    onClick={() => setConfirming(null)}>✕</button>
                </span>
              ) : (
                <span className="flex items-center gap-0.5 pr-1 text-xs text-zinc-500">
                  <button title="rename" aria-label={`Rename ${s.name}`} className={`px-0.5 hover:text-zinc-200 ${TAP}`}
                    onClick={() => setRenaming(s.id)}>✎</button>
                  <button title={atCap ? `max ${MAX_SCENARIOS} scenarios` : "duplicate"}
                    aria-label={atCap ? `Duplicate ${s.name} (at most ${MAX_SCENARIOS} scenarios)` : `Duplicate ${s.name}`}
                    disabled={atCap}
                    className={`px-0.5 hover:text-zinc-200 disabled:opacity-40 ${TAP}`}
                    onClick={() => api.duplicate(s.id)}>⧉</button>
                  <button title="delete" aria-label={`Delete ${s.name}…`} className={`px-0.5 hover:text-red-400 ${TAP}`}
                    onClick={() => setConfirming(s.id)}>✕</button>
                </span>
              )
            )}
          </div>
        );
      })}
      <button onClick={api.create} disabled={atCap}
        title={atCap ? `max ${MAX_SCENARIOS} scenarios` : "add a scenario"}
        className={`rounded-lg px-2 py-1 text-sm text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200 disabled:opacity-40 ${TAP}`}>
        + New
      </button>
    </div>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <Card testId="card-second-house-empty" className="py-10 text-center">
      <div className="text-3xl">🏡</div>
      <p className="mx-auto mt-2 max-w-md text-sm text-zinc-400">
        Model a second home. Compare up to {MAX_SCENARIOS} scenarios — financing,
        carrying costs, short-term-rental income, and which accounts the cash
        comes from. Scenarios are saved in the dashboard's store and sync across devices.
      </p>
      <button onClick={onCreate}
        className="mt-4 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500">
        Create first scenario
      </button>
    </Card>
  );
}

// ---------------------------------------------------------------- worksheet

function Worksheet({ scenario, api, ctx }: {
  scenario: Scenario; api: ScenarioStoreApi; ctx: Ctx;
}) {
  const { data } = useData();
  const i = scenario.inputs;
  const set = (patch: Partial<SecondHouseInputs>) => api.setInputs(scenario.id, patch);

  const d = useMemo(() => {
    const carry = carryBreakdown(i);
    const rental = rentalBreakdown(i);
    const rows = fundingRows(data.accounts, scenario.draws);
    const needed = cashNeeded(i);
    return {
      carry, rental, needed,
      pi: monthlyPI(i),
      netCost: netMonthlyCost(i),
      breakeven: breakevenOccupancyPct(i),
      rows,
      funding: fundingState(rows, needed),
      cashDrawn: cashLikeDrawTotal(rows),
    };
  }, [i, scenario.draws, data.accounts]);

  return (
    <div className="space-y-3">
      <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-5">
        <DealInputsCard i={i} set={set} className="xl:col-span-2" />
        <div data-testid="kpis-second-house" className="grid content-start gap-3 sm:grid-cols-2 xl:col-span-3">
          <StatCard label="Monthly principal & interest" value={fmtUSD0(d.pi)}
            sub={`${fmtUSD0(downPaymentUSD(i))} down (${pctText(i.downPct)}) · 30yr @ ${pctText(i.ratePct, 3)}`} />
          <StatCard label="Cash needed" value={fmtUSD0(d.needed)}
            sub="down payment + closing + post-move-in" />
          <StatCard label="Full monthly carry" value={fmtUSD0(d.carry.total)}
            sub="P&I + tax + insurance + HOA + utilities + maintenance" />
          {/* set against the free cash flow only when that is known: with too little history no tone and no comparison; a
              free cash flow at or below none is a reason for the warning tone, and the tile says it */}
          <StatCard label="Net after rental" value={fmtUSD0(d.netCost)}
            tone={d.netCost <= 0 ? "pos" : ctx.freeCash === null || d.netCost <= ctx.freeCash.monthly ? undefined : "warn"}
            sub={ctx.freeCash === null ? "carry minus expected rental income"
              : ctx.freeCash.monthly > 0
                ? `vs ${fmtUSD0(ctx.freeCash.monthly)}/mo avg free cash flow (${ctx.freeCash.months < 12
                  ? countOf(ctx.freeCash.months, "full month") : "trailing 12mo"})`
                : `no free cash flow to carry it: income did not cover spending over the last `
                  + `${countOf(ctx.freeCash.months, "full month")} (${fmtUSD0(ctx.freeCash.monthly)}/mo)`} />
        </div>
      </div>

      {/* the verdict sits right under the four KPI cards: can we afford it -
          the essentials cushion after the purchase, liquid cash after the draws, PMI */}
      <GuardrailsStrip i={i} d={d} ctx={ctx} />

      {/* explicit base track: an implicit `auto` column would size to the funding
          table's min-content and push the whole page wider than a phone screen */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <FundingCard d={d} ltcgPct={ctx.ltcgPct} generatedAt={ctx.generatedAt} feed={ctx.feed}
          onDraw={(accountId, v) => api.setDraw(scenario.id, accountId, v)} />
        <CostBreakdownCard d={d} ctx={ctx} />
      </div>

      <RentalCard i={i} set={set} d={d} />

      <div className="grid gap-3 xl:grid-cols-3">
        <RateSensitivityCard i={i} pi={d.pi} />
        <SweepCard i={i} />
        <EquityCurveCard i={i} />
      </div>
    </div>
  );
}

function DealInputsCard({ i, set, className }: {
  i: SecondHouseInputs; set: (p: Partial<SecondHouseInputs>) => void; className?: string;
}) {
  return (
    <Card testId="card-deal-inputs" title="The deal" className={className}>
      <div className="flex flex-col">
        <InputRow label="Home price">
          <CurrencyInput value={i.price} onCommit={(v) => set({ price: v })} />
        </InputRow>
        <InputRow label="Down payment" sub={fmtUSD0(downPaymentUSD(i))}>
          <NumberInput value={i.downPct} onCommit={(v) => set({ downPct: v })}
            min={0} max={100} step={0.5} unit="%" />
        </InputRow>
        <SliderRow label="Interest rate" sub="30-yr fixed" value={i.ratePct}
          onCommit={(v) => set({ ratePct: v })} min={3} max={10} step={0.125} unit="%" />
        <InputRow label="Closing costs">
          <CurrencyInput value={i.closingCosts} onCommit={(v) => set({ closingCosts: v })} />
        </InputRow>
        <InputRow label="Post-move-in costs" sub="furniture, repairs">
          <CurrencyInput value={i.postMoveInCosts} onCommit={(v) => set({ postMoveInCosts: v })} />
        </InputRow>
      </div>
      <details open className="mt-2 border-t border-zinc-800 pt-2">
        <summary className="cursor-pointer text-xs font-semibold text-zinc-400">
          Monthly carrying costs
        </summary>
        <div className="mt-1 flex flex-col">
          <InputRow label="Property tax" sub={`${fmtUSD0(i.price * i.taxPctYr / 100 / 12)}/mo`}>
            <NumberInput value={i.taxPctYr} onCommit={(v) => set({ taxPctYr: v })}
              min={0} max={5} step={0.05} unit="%/yr" className="w-16" />
          </InputRow>
          <InputRow label="Home insurance" sub={`${fmtUSD0(i.insuranceYr / 12)}/mo`}>
            <span className="flex items-center gap-1">
              <CurrencyInput value={i.insuranceYr} onCommit={(v) => set({ insuranceYr: v })} className="w-28 sm:w-20" />
              <span className="w-6 text-zinc-500">/yr</span>
            </span>
          </InputRow>
          <InputRow label="HOA">
            <span className="flex items-center gap-1">
              <CurrencyInput value={i.hoaMo} onCommit={(v) => set({ hoaMo: v })} className="w-28 sm:w-20" />
              <span className="w-6 text-zinc-500">/mo</span>
            </span>
          </InputRow>
          <InputRow label="Utilities">
            <span className="flex items-center gap-1">
              <CurrencyInput value={i.utilitiesMo} onCommit={(v) => set({ utilitiesMo: v })} className="w-28 sm:w-20" />
              <span className="w-6 text-zinc-500">/mo</span>
            </span>
          </InputRow>
          <InputRow label="Maintenance reserve" sub={`${fmtUSD0(i.price * i.maintPctYr / 100 / 12)}/mo`}>
            <NumberInput value={i.maintPctYr} onCommit={(v) => set({ maintPctYr: v })}
              min={0} max={5} step={0.25} unit="%/yr" className="w-16" />
          </InputRow>
        </div>
      </details>
    </Card>
  );
}

// ---------------------------------------------------------------- funding

/** the verdict line's tone and the progress bar's colour, by what the plan can claim: green only for a plan that is fully
 *  funded with every account at or above zero; the warning tone for an overdraw or a draw on a balance nobody knows;
 *  red for a shortfall; an excess is told plainly, untoned. The tab's and the comparison's marks take the same tone. */
const VERDICT_TONE: Record<Exclude<FundingVerdict, "unknown">, { text: string; bar: string }> = {
  funded: { text: "text-emerald-400", bar: "bg-emerald-500" },
  overdraw: { text: "text-amber-400", bar: "bg-amber-500" },
  uncovered: { text: "text-amber-400", bar: "bg-amber-500" },
  short: { text: "text-red-400", bar: "bg-red-500" },
  over: { text: "text-zinc-300", bar: "bg-zinc-400" },
};

/** The funding plan: each account that can fund the purchase, its balance and the draw from it. With no such account the
 *  card says so in place of the table; with accounts and no balance known for any, nothing can be drawn, so it names no
 *  funded amount and no shortfall (a sum over balances no one has seen is no "$0 funded"). Otherwise one line says the
 *  plan's verdict (lib/secondHouse fundingState: "fully funded" only when the draws add up and none overdraws its
 *  account). `feed`: some account names a bank-feed id, and only then does the footnote speak of the feed's pulls. */
function FundingCard({ d, ltcgPct, generatedAt, feed, onDraw }: {
  d: Derived; ltcgPct: number; generatedAt: string; feed: boolean;
  onDraw: (accountId: string, v: number) => void;
}) {
  const { funding } = d;
  const pct = Math.min(100, funding.pctFunded * 100);
  const title = "Funding plan — where the cash comes from";
  if (d.rows.length === 0) {
    return (
      <Card testId="card-funding-plan" title={title}>
        <EmptyLine>
          No account to fund it from yet: a checking, savings or brokerage account appears here with its balance once the
          ingest reads one.
        </EmptyLine>
      </Card>
    );
  }
  // why an account shows no balance, in words true of the household: a feed that reported none, or none read yet
  const noBalanceWhy = feed ? "the feed reports no balance for this account" : "no balance is known for this account yet";
  return (
    <Card testId="card-funding-plan" title={title}>
      {funding.verdict !== "unknown" ? (
        <>
          <p data-testid="funding-verdict" className={`mb-1 text-xs ${VERDICT_TONE[funding.verdict].text}`}>
            {fundingVerdictText(funding)}
          </p>
          <div className="mb-3 h-2 overflow-hidden rounded-full bg-zinc-800">
            <div className={`h-full rounded-full transition-[width] ${VERDICT_TONE[funding.verdict].bar}`}
              style={{ width: `${pct}%` }} />
          </div>
        </>
      ) : (
        <div className="mb-3">
          <EmptyLine>
            No balance is known for these accounts yet, so nothing can be drawn: each account&apos;s draw opens once the
            ingest reads its balance.
          </EmptyLine>
        </div>
      )}
      {/* the Before column needs ~580px; this card is a half-width column at xl and a phone's full width
          below md, so the layout follows the CARD's width (a container query), not the viewport's. Below
          the `@sm` container width (a phone) the Draw box WIDENS to hold an 8-digit amount ("99,999,999" at 16px) and the
          Account name (which wraps) and the After figure give way, so Draw and After stay on screen */}
      <TableScroll className="@container" label="Funding plan table">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-zinc-500">
            <tr>
              <th className="py-1 pr-3">Account</th>
              <th className="hidden py-1 pr-3 text-right @xl:table-cell">Before</th>
              <th className="py-1 pr-3 text-right">Draw</th>
              <th className="py-1 pr-2 text-right">After</th>
            </tr>
          </thead>
          <tbody>
            {d.rows.map((r) => (
              <tr key={r.accountId}
                className={`border-t border-zinc-800/60 ${r.before === null ? "opacity-45" : ""}`}>
                <td className="max-w-[9.5rem] py-1.5 pr-3 @max-sm:max-w-[6rem] @max-sm:pr-2 @xl:max-w-52">
                  {/* the name WRAPS in the narrow column (12px in a phone-width card, so most fit two lines): cut to
                      one line, two accounts of one bank ("Sample Bank Card ...0000" / "...1111") read the same */}
                  <div className="text-zinc-200 [overflow-wrap:anywhere] @max-sm:text-xs"><AccountLink id={r.accountId}>{r.name}</AccountLink></div>
                  <div className="flex flex-wrap items-center gap-1 text-[10px] text-zinc-500">
                    {r.owner} · {r.assetClass}
                    {r.retirement && (
                      <Chip>
                        <span className="text-amber-300"
                          title={/529/.test(r.name)
                            ? "529 — non-qualified withdrawals owe income tax + 10% penalty on earnings"
                            : "retirement account — early withdrawals typically owe taxes and penalties"}>
                          penalty ⚠
                        </span>
                      </Chip>
                    )}
                    {r.taxableDrag && r.draw > 0 && (
                      <span className="text-amber-300/80"
                        title="long-term capital gains (LTCG) tax: selling positions may realize gains; the ceiling assumes the whole draw is long-term gain">
                        up to {fmtUSD0(capGainsDragMax(r.draw, ltcgPct))} LTCG @ {pctText(ltcgPct, 0)}
                      </span>
                    )}
                  </div>
                  {/* a narrow card has no room for a Before column beside Draw and After: the balance
                      rides under the name, so the one input and the After column stay on screen */}
                  <div className="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] @xl:hidden">
                    {r.before === null ? (
                      <span className="text-zinc-500" title={noBalanceWhy}>no balance</span>
                    ) : (
                      <>
                        <span className="tabular-nums text-zinc-300">{fmtUSD0(r.before)}</span>
                        <AsOfBadge date={r.asOf} generatedAt={generatedAt} />
                      </>
                    )}
                  </div>
                </td>
                <td className="hidden py-1.5 pr-3 text-right align-top @xl:table-cell">
                  {r.before === null ? (
                    <span className="text-zinc-500" title={noBalanceWhy}>—</span>
                  ) : (
                    <>
                      <div className="tabular-nums text-zinc-300">{fmtUSD0(r.before)}</div>
                      <AsOfBadge date={r.asOf} generatedAt={generatedAt} />
                    </>
                  )}
                </td>
                <td className="py-1.5 pr-3 text-right align-top @max-sm:pr-2">
                  {/* no balance known: nothing new can be drawn, but a draw saved earlier can be lowered or cleared
                      (typing 0 or emptying the box), and the box closes again once it is cleared. Emptying a box that
                      already draws nothing saves nothing */}
                  <CurrencyInput value={r.draw} disabled={r.before === null && r.draw === 0}
                    onCommit={(v) => onDraw(r.accountId, v)}
                    onClear={r.draw > 0 ? () => onDraw(r.accountId, 0) : undefined}
                    className="w-24 @max-sm:w-28" />
                </td>
                <td className={`py-1.5 pr-2 text-right align-top tabular-nums @max-sm:pr-1 @max-sm:text-xs ${
                  r.overdraw ? "font-semibold text-red-400" : "text-zinc-300"}`}
                  title={r.overdraw
                    ? "the draw is more than this account's latest balance — adjust the draw"
                    : undefined}>
                  {r.after === null ? "—" : fmtUSD0(r.after)}
                  {r.overdraw && " ⚠"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      <p className="mt-2 text-[10px] text-faint">
        {feed ? "Balances are each account's latest from the bank feed" : "Balances are each account's latest"} (badges show each
        account's as-of date). Overdraws are flagged, never clamped — if a saved plan outgrew an
        account, you'll see it. Retirement/529 draws are cautioned, not blocked.
      </p>
    </Card>
  );
}

// ---------------------------------------------------------------- guardrails

function GuardrailsStrip({ i, d, ctx }: { i: SecondHouseInputs; d: Derived; ctx: Ctx }) {
  const cash = ctx.liquidCash;
  const essential = ctx.essential;
  const liquidAfter = cash === null ? null : cash - d.cashDrawn;
  // below zero by a cent or more, as the funding verdict counts it (a hair under zero is none, never "$0 short")
  const short = liquidAfter !== null && belowZero(liquidAfter);
  // null, not 0, when no row is essential: "unknown", never "0.0 months of cushion"
  const months = liquidAfter !== null && essential !== null && essential > 0 ? liquidAfter / essential : null;
  return (
    <>
    <div data-testid="kpis-guardrails" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {/* the cushion needs the snapshot's cash and the essentials of enough full months: with either missing it reads "—"
          and says which in short (the snapshot first), and the line under the row says it in full */}
      {liquidAfter === null || essential === null ? (
        <StatCard label="Essentials cushion after purchase" value="—"
          sub={liquidAfter === null ? NO_BALANCES_YET : ctx.noTransactions ? NO_TRANSACTIONS_YET : undefined} />
      ) : short ? (
        // cash draws past today's liquid cash leave no cushion: said in words, never a negative count of months
        <StatCard label="Essentials cushion after purchase" value="none" tone="neg"
          sub={`the cash draws are ${amountText(-liquidAfter)} more than today's liquid cash`} />
      ) : (
        <StatCard label="Essentials cushion after purchase"
          value={fmtMonths(months)}
          tone={months === null ? undefined : months >= 6 ? "pos" : months >= 3 ? "warn" : "neg"}
          sub={`${fmtUSD0(liquidAfter)} liquid ÷ ${essential > 0
            ? `${fmtUSD0(essential)}/mo essentials (as marked on this device)`
            : "no row marked essential on this device"} — counts only cash draws against liquid cash`} />
      )}
      {/* below zero the cash draws are more than the cash there is: a shortfall in words, never a negative balance shown
          as cash */}
      {cash === null || liquidAfter === null
        ? <StatCard label="Liquid cash after cash draws" value="—" sub={NO_BALANCES_YET} />
        : short
          ? <StatCard label="Liquid cash after cash draws" value={`${amountText(-liquidAfter)} short`} tone="neg"
              sub={`${fmtUSD0(cash)} today, ${fmtUSD0(d.cashDrawn)} in cash draws`} />
          : <StatCard label="Liquid cash after cash draws" value={fmtUSD0(liquidAfter)}
              sub={`${fmtUSD0(cash)} today − ${fmtUSD0(d.cashDrawn)} in cash draws`} />}
      {/* what the page leaves out, never what lenders do or charge: no figure here prices mortgage insurance */}
      {/* one tile at every down payment, untoned: the page prices no mortgage insurance, and says only that */}
      <StatCard label="Mortgage insurance" value="Not included"
        sub={pmiLikely(i) ? PMI_NOT_IN_PAYMENT : "None in the payment shown"} />
    </div>
    {liquidAfter === null ? <NoBalances />
      : essential === null ? (ctx.noTransactions ? <NoTransactions /> : (
        <p data-testid="not-enough-history" className="text-xs text-zinc-500">
          The cushion divides by the household's own essential spending, which needs {RUN_RATE_NEEDS}.{" "}
          {historySoFar(ctx.fullMonths, ctx.nextFullMonthEnds)}
        </p>))
      : null}
    </>
  );
}

// ---------------------------------------------------------------- comparison

function ComparisonCard({ scenarios, activeId, liquidCash }: {
  scenarios: Scenario[]; activeId: string | null;
  /** today's liquid cash; null with no snapshot (the cash-left row then reads "—") */
  liquidCash: number | null;
}) {
  const { data } = useData();
  const rows = useMemo(
    () => comparisonRows(scenarios, data.accounts, liquidCash),
    [scenarios, data.accounts, liquidCash]);

  type Row = (typeof rows)[number];
  const metrics: {
    label: string; get: (r: Row) => number | null;
    fmt: (v: number) => string; best?: "min" | "max";
    /** a scenario's value the metric never calls best: a shortfall is no winner, and neither is a scenario that does not
     *  close on a row that rests on its funding */
    notBest?: (r: Row, v: number) => boolean;
    /** the value's own tone, when it says something by itself (a shortfall) */
    tone?: (v: number) => string | undefined;
  }[] = [
    { label: "Home price", get: (r) => r.price, fmt: fmtUSD0 },
    { label: "Down / rate", get: (r) => r.downPct, fmt: () => "" },   // custom-rendered below
    { label: "Cash needed", get: (r) => r.cashNeeded, fmt: fmtUSD0 },
    { label: "Monthly P&I", get: (r) => r.pi, fmt: fmtUSD0, best: "min" },
    { label: "Full carry /mo", get: (r) => r.fullCarry, fmt: fmtUSD0, best: "min" },
    { label: "Net after rental /mo", get: (r) => r.netAfterRental, fmt: fmtUSD0, best: "min" },
    { label: "Breakeven occupancy", get: (r) => r.breakevenOcc,
      fmt: (v) => (v > 100 ? "n/a" : `${v.toFixed(0)}%`), best: "min" },
    { label: "Funded so far", get: (r) => r.drawn, fmt: fmtUSD0 },
    { label: "Funding", get: () => null, fmt: () => "" },   // custom-rendered below: the plan's verdict in a few words
    // below zero (by a cent or more) the cash draws are more than today's liquid cash: a shortfall, in words. What is left
    // rests on the plan's draws, so a scenario that does not close never wins it
    { label: "Liquid cash left", get: (r) => r.remainingLiquid,
      fmt: (v) => (belowZero(v) ? `${amountText(-v)} short` : fmtUSD0(v)),
      best: "max", notBest: (r, v) => belowZero(v) || fundingCloses(r.funding.verdict) !== true,
      tone: (v) => (belowZero(v) ? "text-red-400" : undefined) },
  ];

  const bestFor = (m: (typeof metrics)[number]): number | null => {
    if (!m.best || rows.length < 2) return null;
    const vals = rows.map((r) => ({ r, v: m.get(r) }))
      .filter((x): x is { r: Row; v: number } => x.v !== null && x.v <= (m.label.startsWith("Breakeven") ? 100 : Infinity)
        && !m.notBest?.(x.r, x.v))
      .map((x) => x.v);
    if (!vals.length) return null;
    return m.best === "min" ? Math.min(...vals) : Math.max(...vals);
  };

  return (
    <Card testId="card-scenario-comparison" title="Scenarios side-by-side">
      <TableScroll label="Scenarios side-by-side table" stickyFirst>
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-zinc-500">
            <tr>
              <th className="py-1 pr-3">Metric</th>
              {rows.map((r) => (
                <th key={r.id} className={`py-1 pr-3 text-right ${
                  r.id === activeId ? "text-zinc-200" : ""}`}>{r.name}<DoesNotClose verdict={r.funding.verdict} /></th>
              ))}
            </tr>
          </thead>
          <tbody>
            {metrics.map((m) => {
              const best = bestFor(m);
              return (
                <tr key={m.label} className="border-t border-zinc-800/60">
                  <td className="py-1 pr-3 text-xs text-zinc-400">{m.label}</td>
                  {rows.map((r) => {
                    const v = m.get(r);
                    const isBest = best !== null && v === best && !m.notBest?.(r, v);
                    const funding = m.label === "Funding";
                    const tone = funding ? (r.funding.verdict === "unknown" ? undefined : VERDICT_TONE[r.funding.verdict].text)
                      : v === null ? undefined : m.tone?.(v);
                    return (
                      <td key={r.id}
                        className={`py-1 pr-3 text-right tabular-nums ${
                          r.id === activeId ? "bg-zinc-800/40" : ""} ${
                          isBest ? "font-semibold text-emerald-400" : tone ?? "text-zinc-300"}`}>
                        {m.label === "Down / rate"
                          ? `${pctText(r.downPct)} @ ${pctText(r.ratePct, 3)}`
                          : funding ? fundingVerdictBrief(r.funding) ?? "—"
                          : v === null ? "—" : m.fmt(v)}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableScroll>
      <p className="mt-2 text-[10px] text-faint">
        Green = best of the saved scenarios on that metric. &quot;Funding&quot; is each plan&apos;s verdict: fully funded only
        when the draws add up to the cash needed, none overdraws its account and every draw&apos;s balance is known.
        &quot;Liquid cash left&quot; subtracts only cash draws (the accounts in the Cash class) from today&apos;s liquid cash; a
        shortfall is how far they pass it, and a scenario that does not close is never its best.
      </p>
    </Card>
  );
}

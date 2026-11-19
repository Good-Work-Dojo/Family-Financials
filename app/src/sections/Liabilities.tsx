// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo } from "react";
import EChart from "../components/EChart";
import GatedLink, { WhileShown } from "../components/GatedLink";
import { ManualAssetsLink, NoHomeValue } from "../components/ManualAssetsLink";
import MissingTerms from "../components/MissingLoanTerms";
import RequireData from "../components/RequireData";
import AccountLink from "../components/AccountLink";
import BalanceSpacingNote from "../components/BalanceSpacingNote";
import { Code, EmptyState, NO_BALANCES_YET, NoBalances, OneBalanceEach } from "../components/NoData";
import { Card, Line, StatCard } from "../components/ui";
import { useData } from "../data/DataContext";
import { useSnapshots } from "../data/useSnapshots";
import { balanceTimeOption, onePointEach } from "../lib/balanceCharts";
import { cardHeadroom, fmtUtilization, utilization } from "../lib/credit";
import { WHOLE_PERCENT_STEP } from "../lib/echarts";
import { countOf, fmtDate, fmtPct, fmtUSD, fmtUSD0 } from "../lib/format";
import { cardAccountIds, dailyInterest, type DebtOverview, debtOverview, fmtLoanToValue, missingLoanTerms, mortgageBalance,
  type MortgageSummary, mortgageSummary } from "../lib/lifeSummary";

/** Accounts → Liabilities: what we owe - the total and its parts (mortgage, cards, margin) up top, the mortgage
 *  summary, then the credit cards (headroom, balance and utilization history).
 *  What-ifs live in Planning → House. */
export default function Liabilities() {
  const { data, missing } = useData();
  // the newest snapshot; null for a household with none yet, whose debt tiles say so
  const current = useSnapshots().latest;
  const { series, accounts } = data.balances;
  const cardIds = useMemo(() => Object.keys(series)
    .filter((id) => series[id].length > 0 && accounts[id]?.kind === "credit-card")
    .sort((a, b) => (accounts[a]?.name || a).localeCompare(accounts[b]?.name || b)), [series, accounts]);
  // each card's newest point: the feed's available credit, else limit − owed (lib/credit.ts)
  const head = useMemo(() => cardHeadroom(cardIds.map((id) => series[id][series[id].length - 1])), [cardIds, series]);
  // card debt is known only with a balance history that holds a card; otherwise it is left OUT of the total and
  // named as missing - never counted as $0
  const cardsKnown = !missing.has("balances_timeseries.json") && cardIds.length > 0;
  // with cards in the snapshot (include_credit_cards) the total reads the snapshot's card rows, never the history on top of
  // them; the headroom, limits and charts below stay on the history in both modes
  const includeCards = data.assumptions.net_worth.include_credit_cards;
  const cardAccounts = useMemo(() => cardAccountIds(data.accounts), [data.accounts]);
  const debt = useMemo(() => debtOverview(data.assumptions, current, cardsKnown ? head.debt : null, includeCards, cardAccounts),
    [data.assumptions, current, cardsKnown, head.debt, includeCards, cardAccounts]);
  // the card tile counts the cards its figure reads, and those of them in credit (owing nothing, so adding nothing to
  // it): with cards in the snapshot, the card accounts it holds a row for
  const { cardsCounted, cardsInCredit } = useMemo(() => {
    if (!includeCards) return { cardsCounted: cardIds.length, cardsInCredit: head.inCredit };
    const rows = (current?.rows ?? []).filter((r) => r.account_id !== null && cardAccounts.has(r.account_id));
    return { cardsCounted: new Set(rows.map((r) => r.account_id)).size,
      cardsInCredit: new Set(rows.filter((r) => r.value > 0).map((r) => r.account_id)).size };
  }, [includeCards, current, cardAccounts, cardIds, head.inCredit]);
  return (
    <div className="space-y-4">
      {debt === null ? <NoSnapshotKpis hasMortgage={data.assumptions.mortgage !== null} /> : (
        <LiabilityKpis debt={debt} head={head} cardCount={cardsCounted} cardsInCredit={cardsInCredit}
          historyCards={cardIds.length} includeCards={includeCards}
          hasMortgage={data.assumptions.mortgage !== null} hasCards={cardAccounts.size > 0}
          hasMargin={data.assumptions.household.margin_account !== null}
          historyLoaded={!missing.has("balances_timeseries.json")} />
      )}
      <MortgageCard />
      <RequireData files={["balances_timeseries.json"]} what="Credit cards">
        <CreditCards cardIds={cardIds} hasCards={cardAccounts.size > 0} />
      </RequireData>
    </div>
  );
}

/** a tile's line in the row's one style: sentence case, and a full stop only on a full sentence (none here is one) */
const lineCase = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);

/** the mortgage tile's lines with no balance: no account is marked as the mortgage, or the feed has not reported it */
const NO_MORTGAGE = "No mortgage account";
const NO_MORTGAGE_BALANCE = "No balance from the bank feed yet";

/** the debt tiles of a household with no snapshot yet: the margin and other debt classes are the snapshot's, so no total
 *  is known; every tile reads "—" and says why in a few words (the mortgage tile, whose balance is the mortgage
 *  account's own feed row, says what the tile says with a snapshot: there is no such account, as the card below says,
 *  or the feed has not reported it), and the long line runs under the row, full width */
function NoSnapshotKpis({ hasMortgage }: {
  /** an account is the mortgage (assumptions.json `mortgage` is not null) */
  hasMortgage: boolean;
}) {
  return (
    <>
      <div data-testid="kpis-liabilities" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <StatCard testId="kpi-total-debt" label="Total debt" value="—" sub={NO_BALANCES_YET} />
        <StatCard testId="kpi-mortgage-balance" label="Mortgage" value="—" sub={hasMortgage ? NO_MORTGAGE_BALANCE : NO_MORTGAGE} />
        <StatCard testId="kpi-card-debt" label="Credit cards" value="—" sub={NO_BALANCES_YET} />
        <StatCard testId="kpi-margin-debt" label="Margin" value="—" sub={NO_BALANCES_YET} />
        <StatCard testId="kpi-available-credit" label="Available credit" value="—" sub={NO_BALANCES_YET} />
      </div>
      <NoBalances />
    </>
  );
}

/** the card tiles' lines when there is no figure, a few words each as every KPI tile's: no credit-card account at all
 *  (the card below the row says what brings one), cards with nothing recorded, or (cards in net worth) cards the balance
 *  history holds that the newest snapshot has no row for yet — the available-credit tile beside it reads that history */
const NO_CARD_YET = "No credit card yet";
const NO_CARD_BALANCE = "No credit-card balance yet";
const NO_CARD_ROW = "Not in the newest snapshot yet";
const NO_CARD_HISTORY = "No credit-card balance history yet";

/** the KPI row's widest step by its number of tiles (literal class names, so Tailwind sees each) */
const LG_COLS: Record<number, string> = { 3: "lg:grid-cols-3", 4: "lg:grid-cols-4", 5: "lg:grid-cols-5" };

/** the answer to "what do we owe": the total, then each part from its one source (the margin tile opens Investments).
 *  The two card tiles are there for every household: with no figure they read "—" and say what is missing. A tile
 *  whose figure reads the balance history is left out while that file did not load (the missing-data card below says
 *  so). */
function LiabilityKpis({ debt, head, cardCount, cardsInCredit, historyCards, includeCards, hasMortgage, hasCards, hasMargin,
  historyLoaded }: {
  debt: DebtOverview; head: ReturnType<typeof cardHeadroom>;
  /** the cards the card tile's figure reads */
  cardCount: number;
  /** of them, the cards in credit: each owes nothing, so the figure (what is owed) leaves its credit out */
  cardsInCredit: number;
  /** the card accounts the balance history holds: available credit reads them, so without one it has no figure */
  historyCards: number;
  includeCards: boolean;
  /** an account is the mortgage (assumptions.json `mortgage` is not null) */
  hasMortgage: boolean;
  /** the household has a credit-card account */
  hasCards: boolean;
  /** the household has a margin account (assumptions.json `household.margin_account`) */
  hasMargin: boolean;
  /** the balance history loaded (it may be empty) */
  historyLoaded: boolean;
}) {
  const mb = debt.mortgage;
  const cardsKnown = debt.cards !== null;
  // a household with no credit-card account has no card balance to be missing: nothing is said of one
  const gaps = debt.unknown.filter((u) => u !== "cards" || hasCards)
    .map((u) => (u === "cards" ? "credit-card balances" : "the mortgage balance"));
  // the card tile reads the snapshot's card rows with cards in net worth, else the history
  const cardTile = cardsKnown || includeCards || historyLoaded;
  const tiles = 3 + (cardTile ? 1 : 0) + (historyLoaded ? 1 : 0);
  return (
    <div data-testid="kpis-liabilities" className={`grid gap-3 sm:grid-cols-2 ${LG_COLS[tiles]}`}>
      <StatCard testId="kpi-total-debt" label="Total debt"
        value={debt.parts.length ? fmtUSD0(debt.total) : "—"} tone={debt.total > 0 ? "neg" : undefined}
        // cards are not in a snapshot's net-worth debt unless the household counts them there: say how the two debt figures relate
        sub={lineCase([debt.parts.length ? debt.parts.join(" + ") + (cardsKnown && !includeCards ? " = net-worth debt + credit-card balances" : "") : "no debt balance reported",
          gaps.length ? `${gaps.join(" and ")} not available` : null].filter(Boolean).join(" · "))} />
      <StatCard testId="kpi-mortgage-balance" label="Mortgage"
        value={mb ? fmtUSD0(mb.balance) : "—"} tone={mb && mb.balance > 0 ? "neg" : undefined}
        sub={mb ? lineCase(`${mb.balance === 0 ? "paid off · " : ""}from the bank feed${mb.asOf ? ` · ${fmtDate(mb.asOf)}` : ""}`)
          : hasMortgage ? NO_MORTGAGE_BALANCE : NO_MORTGAGE} />
      {debt.cards !== null ? (
        <StatCard testId="kpi-card-debt" label="Credit cards" value={fmtUSD0(debt.cards)}
          tone={debt.cards > 0 ? "neg" : "pos"}
          sub={[countOf(cardCount, "credit card"), cardsInCredit > 0 ? countOf(cardsInCredit, "in credit", "in credit") : null]
            .filter(Boolean).join(" · ")} />
      ) : cardTile && (
        <StatCard testId="kpi-card-debt" label="Credit cards" value="—"
          sub={!hasCards ? NO_CARD_YET : includeCards && historyCards > 0 ? NO_CARD_ROW : NO_CARD_BALANCE} />
      )}
      {/* with no margin account (and nothing owed on margin) there is no margin balance to total: "—", as on Investments */}
      <StatCard testId="kpi-margin-debt" label="Margin" value={!hasMargin && debt.margin === 0 ? "—" : fmtUSD0(debt.margin)}
        tone={debt.margin > 0 ? "neg" : undefined} href="#/accounts/investments"
        sub={debt.margin > 0 ? "Brokerage margin loan · Investments →"
          : hasMargin ? "No margin balance · Investments →" : "No margin account · Investments →"} />
      {historyCards > 0 ? (
        <StatCard testId="kpi-available-credit" label="Available credit"
          value={head.available === null ? "—" : fmtUSD0(head.available)}
          sub={head.available === null
            // no card has a limit or an available figure (never "not reported by issuer", and never $0)
            ? "No credit limit known"
            : lineCase([head.utilization === null ? null : `${fmtUtilization(head.utilization)} utilized`,
               head.derived ? "limit − balance" : null,
               head.known < head.cards ? `${head.known} of ${countOf(head.cards, "credit card")}` : null].filter(Boolean).join(" · "))} />
      ) : historyLoaded && (
        <StatCard testId="kpi-available-credit" label="Available credit" value="—" sub={hasCards ? NO_CARD_HISTORY : NO_CARD_YET} />
      )}
    </div>
  );
}

function CreditCards({ cardIds, hasCards }: {
  cardIds: string[];
  /** the household has a credit-card account */
  hasCards: boolean;
}) {
  const { data } = useData();
  const { series, accounts } = data.balances;
  // a card's colour is its position in the household's account list (lib/balanceCharts.ts balanceLines), the same in both
  // charts below and on every other page that draws it
  const accountIds = useMemo(() => data.accounts.map((a) => a.id), [data.accounts]);
  // utilisation by the one definition (lib/credit.ts), drawn only where a limit is known: a point with none is left
  // out, never drawn at 0%
  const utilSeries = useMemo(() => Object.fromEntries(cardIds.map((id) =>
    [id, series[id].filter((p) => utilization(p.balance, p.limit) !== null)])), [cardIds, series]);
  const utilIds = useMemo(() => cardIds.filter((id) => utilSeries[id].length > 0), [cardIds, utilSeries]);
  // plotted as the ratio itself: the axis in whole percents, the tooltip at the tile's one precision (fmtUtilization)
  const utilOption = useMemo(() => {
    // utilization and the amount owed keep zero on their axes: zero is the goal, and the note's 30% is read against it
    const o = balanceTimeOption(utilSeries, accounts, utilIds, (p) => utilization(p.balance, p.limit)!, accountIds,
      { fmt: { tooltip: fmtUtilization, axis: (v) => fmtPct(v, 0), step: () => WHOLE_PERCENT_STEP }, keepZero: true });
    return { ...o, tooltip: { ...o.tooltip, valueFormatter: (v: number | null | undefined) => (v == null ? "—" : fmtUtilization(v)) } };
  }, [utilSeries, accounts, utilIds, accountIds]);
  // no card account has history: one card says so, in place of the two card charts, instead of printing a green
  // "$0 / 0 cards" over nothing. With no credit-card account at all it is the page's full line about cards (the tiles
  // above say it short): what to add, and the keys as a hint. A card's balance comes from the bank feed alone (the card
  // statement format carries none), so the action is the account and its feed id together.
  if (cardIds.length === 0) {
    return (
      <Card testId="card-no-credit-cards" title="Credit cards">
        {!hasCards ? (
          <EmptyState>
            No credit card yet. Add a credit-card account connected to the bank feed and its balance, available credit and
            utilization appear here after the next refresh (<Code>kind: credit-card</Code> and <Code>simplefin_id:</Code> in
            {" "}<Code>accounts.yaml</Code>).
          </EmptyState>
        ) : (
          <EmptyState>
            No credit-card balance history yet. A credit card's balance comes from the bank feed alone (its statement files
            state none): once a card is connected to it, its balance, available credit and utilization appear here after
            the next refresh that reports them (<Code>simplefin_id:</Code> on the card in <Code>accounts.yaml</Code>).
          </EmptyState>
        )}
      </Card>
    );
  }
  return (
    <>
      <Card testId="card-card-balances" title="Credit-card balance over time"
        right={<span className="text-[10px] text-zinc-500">plotted as amount owed (↑ = more debt)</span>}>
        <EChart option={balanceTimeOption(series, accounts, cardIds, (p) => -p.balance, accountIds, { keepZero: true })}
          height={300} />
        {onePointEach(series, cardIds) && <OneBalanceEach />}
        <BalanceSpacingNote series={series} ids={cardIds} />
      </Card>
      {utilIds.length > 0 ? (
        <Card testId="card-utilization" title="Credit utilization"
          right={<span className="text-[10px] text-zinc-500">balance ÷ limit (keep under 30%)</span>}>
          <EChart option={utilOption} height={260} />
          {onePointEach(utilSeries, utilIds) && <OneBalanceEach />}
          <BalanceSpacingNote series={utilSeries} ids={utilIds} />
        </Card>
      ) : (
        <Card testId="card-utilization" title="Credit utilization">
          <EmptyState>
            No credit limit is known for any credit card, so utilization cannot be shown. Add each card's limit and it
            appears here after the next ingest (<Code>credit_limit:</Code> on the card in <Code>accounts.yaml</Code>).
          </EmptyState>
        </Card>
      )}
    </>
  );
}

/** "$2,000 + $450/mo", or "$2,000/mo · P&I only" when the loan terms state no escrow (never "+ $0") */
function payment(m: Pick<MortgageSummary, "monthlyPI" | "monthlyEscrow">): string {
  const pi = fmtUSD0(m.monthlyPI);
  return m.monthlyEscrow == null ? `${pi}/mo · P&I only` : `${pi} + ${fmtUSD0(m.monthlyEscrow)}/mo`;
}

/** the mortgage summary (lib/lifeSummary mortgageSummary); reads only the assumptions, the current snapshot's House-class
 *  rows (the home's value) and the manifest's stamp, so it survives a missing balance history. The balance is the bank
 *  feed's and the rate, payment and escrow are the loan terms, and the card says so; a term the account does not state
 *  says what to add (MissingTerms), never a number, and so does a home the snapshot does not hold. */
function MortgageCard() {
  const { data } = useData();
  const mg = data.assumptions.mortgage;
  const generatedAt = data.manifest.generated_at;
  const current = useSnapshots().latest;
  const mort = useMemo(() => mortgageSummary(data.assumptions, current, generatedAt),
    [data.assumptions, current, generatedAt]);
  const mb = mortgageBalance(data.assumptions);
  // the loan account the balance and the terms belong to has a page of its own
  const account = mg ? data.accounts.find((a) => a.id === mg.account_id) : undefined;
  const missing = mg ? missingLoanTerms(mg) : [];
  const asOf = mb?.asOf ? `, as of ${fmtDate(mb.asOf)}` : "";
  const source = <>from the bank feed{account && <> (<AccountLink id={account.id}>{account.name}</AccountLink>)</>}{asOf}</>;
  const rate = mg?.rate_pct ?? null;
  const pay = mg?.monthly_payment ?? null;
  const escrow = mg?.monthly_escrow != null && mg.monthly_escrow > 0 ? mg.monthly_escrow : null;
  return (
    <Card testId="card-mortgage-summary" title="Mortgage"
      right={<GatedLink to="/planning/home" whenOff={null}
        className="inline-flex min-h-[40px] items-center text-xs text-blue-400 hover:underline md:min-h-0">what-ifs →</GatedLink>}>
      {mg === null ? (
        // a mortgage is a loan account marked as one: with no loan account there is nothing to mark
        !data.accounts.some((a) => a.kind === "loan") ? (
          <p className="text-xs text-zinc-500">No loan account, so no mortgage is tracked.</p>
        ) : (
          <p className="text-xs text-zinc-500">
            No mortgage. Mark the loan account that is the mortgage and its balance and terms appear here after the next
            ingest (<Code>mortgage: true</Code> on the account in <Code>accounts.yaml</Code>).
          </p>
        )
      ) : mb === null ? (
        <>
          <p data-testid="mortgage-no-balance" className="text-xs text-zinc-500">
            No balance from the bank feed yet{account ? <> for <AccountLink id={account.id}>{account.name}</AccountLink></> : ""}.
          </p>
          <MissingTerms terms={missing} />
        </>
      ) : mb.balance === 0 ? (
        <p data-testid="mortgage-paid-off" className="text-xs text-zinc-300">Paid off: the bank feed shows nothing owed{asOf}.</p>
      ) : mort ? (
        <>
          <ul className="space-y-1.5 pt-1 text-xs">
            <Line k="Balance" v={fmtUSD0(mort.balance)} />
            <Line k="Rate" v={`${mort.ratePct}%`} />
            <Line k={mort.monthlyEscrow == null ? "P&I" : "P&I + escrow"} v={payment(mort)} />
            <Line k="Payoff at current pace" v={String(mort.payoffYear)} />
            <Line k="Home equity" v={mort.equity === null ? "—" : `${fmtUSD0(mort.equity)} (LTV ${fmtLoanToValue(mort.ltv)})`} />
            <Line k="Interest cost" v={`${fmtUSD(mort.dailyInterest)}/day`} />
          </ul>
          {/* the loan's own rate and what a prepaid dollar saves at it: no comparison with any market instrument, which
              nothing in the data prices */}
          <p className="mt-2 text-[10px] text-faint">
            at {mort.ratePct}%, each dollar prepaid saves {mort.ratePct}% a year in interest
            <WhileShown to="/planning/home">
              {" — the extra-payment what-if is in "}
              <GatedLink to="/planning/home" className="text-blue-400 hover:underline">Planning → House</GatedLink>
            </WhileShown>
            {" · balance "}{source}{mg.monthly_escrow == null
              ? " · rate and payment from your loan terms" : " · rate, payment and escrow from your loan terms"}
            {mort.homeValue !== null && <> · home value as entered by hand, in <ManualAssetsLink /></>}
          </p>
          {mort.homeValue === null && <NoHomeValue className="mt-2 text-[10px] text-faint" />}
          <MissingTerms terms={missing} />
        </>
      ) : (
        // a balance with the rate or the payment missing, or a payment that does not cover a month's interest: the card
        // prints what is known - the balance, the rate and its interest cost, the payment - and what is missing
        <>
          <ul className="space-y-1.5 pt-1 text-xs">
            <Line k="Balance" v={fmtUSD0(mb.balance)} />
            {rate !== null && <Line k="Rate" v={`${rate}%`} />}
            {rate !== null && <Line k="Interest cost" v={`${fmtUSD(dailyInterest(mb.balance, rate))}/day`} />}
            {pay !== null && <Line k={escrow === null ? "P&I" : "P&I + escrow"}
              v={payment({ monthlyPI: pay, monthlyEscrow: escrow })} />}
          </ul>
          <div data-testid="mortgage-summary-partial">
            <p className="mt-2 text-[10px] text-faint">
              balance {source}{rate !== null || pay !== null ? " · the rest from your loan terms" : ""}
            </p>
            {rate !== null && pay !== null && (
              <p className="mt-2 text-[10px] text-faint">
                The monthly payment in your loan terms does not pay the balance down, so the payoff year is not
                worked out.
              </p>
            )}
            <MissingTerms terms={missing} />
          </div>
        </>
      )}
    </Card>
  );
}

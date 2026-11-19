// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { type ReactNode, useMemo, useState } from "react";
import AccountLink from "../components/AccountLink";
import BalanceSpacingNote from "../components/BalanceSpacingNote";
import EChart from "../components/EChart";
import {
  Code, EmptyState, NO_BALANCES_YET, NoBalances, NO_TRANSACTIONS_YET, NOT_ENOUGH_HISTORY, OneBalanceEach,
} from "../components/NoData";
import PrefEditorRow from "../components/PrefEditorRow";
import RequireData from "../components/RequireData";
import { AsOfBadge, Card, DoneButton, EditButton, LocalOnlyNotice, StatCard, TableScroll } from "../components/ui";
import { useData } from "../data/DataContext";
import { useSnapshots } from "../data/useSnapshots";
import { isInvestmentAccount } from "../lib/accounts";
import { monthInProgress } from "../lib/agg";
import { balanceLines, balanceTimeOptionOf, onePointEach } from "../lib/balanceCharts";
import { type ChartTable, timeTable } from "../lib/chartTable";
import { countOf, fmtDate, fmtPct, fmtPoints, fmtUSD0, humanizeDates } from "../lib/format";
import { balanceOnlyHoldings } from "../lib/holdings";
import { marginBreakdown } from "../lib/lifeSummary";
import { passiveIncomeTTM } from "../lib/passiveIncome";
import { useScrollToCardParam } from "../lib/scrollTargets";
import { CLASS_COLORS } from "../lib/tokens";
import { useVocab } from "../lib/useVocab";
import TargetMixCard from "./investments/TargetMixCard";

/** Accounts → Investments, in the order the questions are asked: the KPIs, the top-position concentration, the target mix
 *  (a suggestion until the family saves one), the asset-class targets under it (the household's own, or a line saying
 *  none is stated), the holdings (the most-read card), THEN the account values over time. */
export default function Investments() {
  const { missing } = useData();
  // the account-values chart reads balances_timeseries.json alone, so it is gated on that file and survives the page's own
  // (holdings) being absent: with it present the chart sits inside the page, after the holdings; with it absent the page is
  // the unavailable card and the chart follows it. `short`: the holdings card above it already said there is no
  // investment account, in full
  const balances = (short: boolean) => (
    <RequireData files={["balances_timeseries.json"]} what="Investment balances over time">
      <InvestmentBalancesCard short={short} />
    </RequireData>
  );
  const pageAbsent = PAGE_FILES.some((f) => missing.has(f));
  return (
    <div className="space-y-4">
      <RequireData files={PAGE_FILES} what="Investments">
        <InvestmentsPage balances={balances} />
      </RequireData>
      {pageAbsent && balances(false)}
    </div>
  );
}

const PAGE_FILES = ["holdings.json"] as const;

/** no investment account: what the first card of the page that needs one says; the card after it says the short form.
 *  A brokerage's balance and positions come from the bank feed alone (no statement format reads a brokerage), so the
 *  action is the account and its feed id together. */
const NoInvestmentAccount = () => (
  <>
    No investment account yet. Add a brokerage account connected to the bank feed and it appears here after the next
    refresh (<Code>kind: brokerage</Code> and <Code>simplefin_id:</Code> in <Code>accounts.yaml</Code>).
  </>
);
const NO_INVESTMENT_ACCOUNT_YET = "No investment account yet.";
/** "A", "A and B", "A, B and C" */
const andList = (names: readonly string[]): string =>
  names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;

/** investment accounts with neither positions nor a balance in the newest snapshot */
const NO_HOLDINGS = "No holdings yet. Each investment account appears here once it reports its positions or a balance.";

function InvestmentsPage({ balances }: { balances: (short: boolean) => ReactNode }) {
  const { data } = useData();
  const vocab = useVocab();
  // a `?card=` link lands on the target-mix card (lib/scrollTargets.ts), which this page renders
  useScrollToCardParam();
  // the twelve full months before the month in progress (lib/agg.ts monthInProgress), or as many as the data holds; null
  // with no full month: no window to sum, so the KPI says so instead of $0 over months before the data
  const passive = useMemo(() => passiveIncomeTTM(data.transactions, vocab, data.cashflow, monthInProgress(data)),
    [data, vocab]);

  const holdings = data.holdings;
  // the newest snapshot; null for a household with none yet, whose totals and holdings say so instead of a figure
  const latest = useSnapshots().latest;

  // Balance-only investment accounts (a feed reports a balance but no positions — see
  // lib/holdings.ts): folded in as single balance-level lines so this page neither understates the
  // portfolio nor treats one spouse's 401k differently from the other's.
  const balanceOnly = useMemo(() => balanceOnlyHoldings(latest, holdings), [latest, holdings]);
  const balanceOnlyNames = useMemo(
    () => new Set(balanceOnly.map((h) => h.account)), [balanceOnly]);

  // real security-level holdings + balance-only lines = the full invested base
  const allHoldings = useMemo(() => [...holdings, ...balanceOnly], [holdings, balanceOnly]);
  const total = allHoldings.reduce((s, h) => s + h.value, 0);

  const accountIds = useMemo(() => new Set(data.accounts.map((a) => a.id)), [data.accounts]);
  const hasInvestmentAccount = useMemo(() => data.accounts.some(isInvestmentAccount), [data.accounts]);
  // a live margin account among the holdings: its positions are counted here gross, before the loan Balances and the
  // balances chart take off, so the figures that read it say which they are
  const heldOnMargin = latest !== null
    && allHoldings.some((h) => h.account_id !== null && marginBreakdown(latest, h.account_id) !== null);
  const byAccount = useMemo(() => {
    const m = new Map<string, typeof allHoldings>();
    for (const h of allHoldings) {
      let arr = m.get(h.account);
      if (!arr) m.set(h.account, (arr = []));
      arr.push(h);
    }
    return [...m.entries()].sort((a, b) =>
      b[1].reduce((s, h) => s + h.value, 0) - a[1].reduce((s, h) => s + h.value, 0));
  }, [allHoldings]);

  const marginInterest = useMemo(() => data.transactions
    .filter((t) => vocab.isMarginInterest(t.subcategory))
    .reduce((s, t) => s - t.amount, 0), [vocab, data.transactions]);
  const margin = latest === null ? null : Math.abs(latest.by_class["Margin"] ?? 0);
  // a margin account (the `margin: true` brokerage), or margin owed without one named: else there is no margin to show
  const hasMargin = data.assumptions.household.margin_account !== null || (margin ?? 0) > 0;

  // An investment account with no transaction at all reports balances only: the dividends and margin interest only its
  // rows could show are unknown for it, never $0. A sum of none of them with every investment account in that state
  // reads "—"; with some account's rows it is a real figure (a real $0 included).
  const investmentAccounts = useMemo(() => data.accounts.filter(isInvestmentAccount), [data.accounts]);
  const noFeed = useMemo(() => investmentAccounts.filter((a) => a.txn_data_start === null).map((a) => a.name),
    [investmentAccounts]);
  const brokerageUnknown = investmentAccounts.length > 0 && noFeed.length === investmentAccounts.length;
  /** the passive tile's line. With every investment account balance-only, every interest row is a bank account's, so
   *  it is named bank interest; dividends no row shows read "—", followed at once by why and by what that leaves the
   *  headline (it leaves them out). With some investment account's rows, or none at all, the parts are real, and an
   *  account still without a feed is named as not counted. */
  const passiveLine = (p: { dividends: number; interest: number }): string => {
    if (!brokerageUnknown) {
      return `dividends ${fmtUSD0(p.dividends)} · interest ${fmtUSD0(p.interest)}`
        + (noFeed.length > 0 ? ` · ${andList(noFeed)}: no transaction feed, not counted` : "");
    }
    const line = `bank interest ${fmtUSD0(p.interest)}`;
    return p.dividends !== 0 ? `${line} · dividends ${fmtUSD0(p.dividends)}`
      : `${line} · dividends — (no transaction feed for ${andList(noFeed)}, so the figure leaves them out)`;
  };
  // the margin account: the `margin: true` brokerage, else the account a margin row of the snapshot belongs to
  const marginAccount = useMemo(() => {
    const named = data.assumptions.household.margin_account;
    const byName = named === null ? undefined : data.accounts.find((a) => a.name === named);
    const row = latest?.rows.find((r) => r.asset_class === "Margin" && r.account_id !== null);
    return byName ?? (row ? data.accounts.find((a) => a.id === row.account_id) : undefined);
  }, [data.accounts, data.assumptions.household.margin_account, latest]);
  // margin interest is known from the margin account's own rows; with none of them it is "—" and the tile says why: the
  // account has no transaction feed, or no account can be told to be the margin account
  const marginLine = marginInterest !== 0 || (marginAccount !== undefined && marginAccount.txn_data_start !== null)
    ? `${fmtUSD0(marginInterest)} margin interest paid to date`
    // with a bank feed the account may simply have had no transaction yet (a feed connected days ago): no feed is claimed
    : marginAccount !== undefined ? (data.manifest.setup.feed ? "margin interest — · no transaction from this account yet"
      : "margin interest — · no transaction feed for this account")
    : data.assumptions.household.margin_account !== null
      ? `margin interest — · ${data.assumptions.household.margin_account} is not among the accounts`
      : "margin interest — · no account is marked as the margin account";

  // top-position concentration: every holding above 10% of the investable base (balance-only lines included in the base);
  // a base of 0 has no share to weigh
  const topPositions = total > 0 ? holdings.filter((h) => h.value / total > 0.1) : [];

  return (
    <div className="space-y-4">
      {/* with no snapshot the two snapshot tiles read "—" and say so in short (the concentration line below says it in
          full); passive income is the transactions' */}
      {/* a total over no account is no $0: "—" and what is missing; the counts go through the count formatter */}
      <div data-testid="kpis-investments" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {latest === null
          ? <StatCard label="Total invested assets" value="—" sub={NO_BALANCES_YET} />
          : byAccount.length === 0
            ? <StatCard label="Total invested assets" value="—" sub={hasInvestmentAccount ? "no holdings yet" : "no investment account"} />
            : <StatCard label="Total invested assets" value={fmtUSD0(total)}
                sub={`across ${countOf(byAccount.length, "account")} (excl. house & bank cash)`
                  + (heldOnMargin ? " · positions before the margin loan" : "")} />}
        {/* the label counts the months the window really spans; with no full month there is none */}
        <StatCard label={passive === null ? "Passive income" : `Passive income (${passive.months} mo)`}
          value={passive === null ? "—" : fmtUSD0(passive.total)}
          sub={passive === null ? (data.transactions.length === 0 ? NO_TRANSACTIONS_YET : NOT_ENOUGH_HISTORY)
            : passiveLine(passive)} />
        {margin === null
          ? <StatCard label="Margin debt" value="—" sub={NO_BALANCES_YET} />
          : !hasMargin
            ? <StatCard label="Margin debt" value="—" sub="no margin account" />
            : <StatCard label="Margin debt" value={fmtUSD0(margin)} tone={margin > 0 ? "neg" : undefined} sub={marginLine} />}
      </div>

      {/* the first card that lacks holdings says what is missing in full; the holdings card below says it short */}
      <div data-testid="card-concentration"
        className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-3 text-xs leading-relaxed text-zinc-300">
        <div className="mb-1 font-semibold">Positions &gt;10% of investable assets</div>
        {latest === null ? <NoBalances />
          : allHoldings.length === 0 ? (hasInvestmentAccount ? "No holdings yet, so no position to weigh." : <NoInvestmentAccount />)
          : total <= 0 ? "The holdings add up to zero, so no position has a share."
          : topPositions.map((h) => `${h.symbol ?? humanizeDates(h.name)} ${fmtPct(h.value / total)}`).join(" · ")
            || "No single position is above 10% of investable assets."}
      </div>

      <TargetMixCard holdings={holdings} unclassified={balanceOnly} />

      <ClassDriftCard />

      <Card testId="card-holdings" title="Holdings by account">
        {latest === null ? <NoBalances short /> : byAccount.length === 0 ? (
          <EmptyState>{hasInvestmentAccount ? NO_HOLDINGS : NO_INVESTMENT_ACCOUNT_YET}</EmptyState>
        ) : <>
        <div className="space-y-3">
          {byAccount.map(([account, hs]) => {
            const sub = hs.reduce((s, h) => s + h.value, 0);
            return (
              <details key={account} className="rounded-lg border border-zinc-800">
                {/* name | value on the first line; the "as of" chip under the name on a phone (beside the value it crowded the card's
                    edge and pushed the name to three lines), after the value from md up */}
                <summary className="flex cursor-pointer flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2 text-sm md:flex-nowrap">
                  <span className="min-w-0 flex-1 basis-0 font-medium">
                    {/* the account's own page, when the registry knows it (a balance-only line with no feed account has none) */}
                    {accountIds.has(hs[0].account_id ?? "")
                      ? <AccountLink id={hs[0].account_id!} onClick={(e) => e.stopPropagation()}>{account}</AccountLink> : account}
                    <span className="ml-2 text-xs text-zinc-500">{hs[0].institution} · {hs[0].owner}</span>
                    {balanceOnlyNames.has(account) && (
                      <span className="ml-2 whitespace-nowrap rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] font-normal text-zinc-400"
                        title="The bank feed reports a balance for this account but none of the positions in it">
                        balance only
                      </span>
                    )}
                  </span>
                  <span className="tabular-nums">
                    {fmtUSD0(sub)}
                    {/* a live margin account's positions: Balances and the chart below hold it net of the loan */}
                    {latest !== null && hs[0].account_id !== null && marginBreakdown(latest, hs[0].account_id) !== null && (
                      <span data-testid="holdings-gross" className="ml-1 text-[10px] text-zinc-500">before the margin loan</span>
                    )}
                  </span>
                  <span data-testid="holdings-asof" className="order-last basis-full md:order-none md:basis-auto">
                    <AsOfBadge date={hs[0].as_of === "unknown" ? null : hs[0].as_of}
                      generatedAt={data.manifest.generated_at} />
                  </span>
                </summary>
                <TableScroll label="Holdings table">
                <table className="w-full text-xs">
                  {/* the columns have no visible header (the rows read as a list); the words are for a screen reader, who
                      otherwise hears a bare number for the quantity */}
                  <thead>
                    <tr><th scope="col" className="sr-only">Symbol</th><th scope="col" className="sr-only">Name</th>
                      <th scope="col" className="sr-only">Quantity</th><th scope="col" className="sr-only">Value</th></tr>
                  </thead>
                  <tbody>
                    {[...hs].sort((a, b) => b.value - a.value).map((h, i) => (
                      <tr key={`${account}|${h.symbol ?? h.name}|${i}`} className="border-t border-zinc-800/60">
                        <td className="py-1 pl-3">{h.symbol ?? "—"}</td>
                        <td className="text-zinc-400">{h.name.slice(0, 50)}</td>
                        <td className="text-right tabular-nums">{h.quantity ?? ""}</td>
                        <td className="pr-3 text-right tabular-nums">{fmtUSD0(h.value)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </TableScroll>
              </details>
            );
          })}
        </div>
        <p className="mt-2 text-[10px] text-faint">
          Each account is valued here from its positions; Accounts → Balances shows the bank feed's own balance, so a few accounts
          read differently on the two pages (an account with margin debt is gross of it here, and a balance-only line has no positions).
        </p>
        </>}
      </Card>

      {balances(latest !== null && byAccount.length === 0 && !hasInvestmentAccount)}
    </div>
  );
}

/** the card's line when the household has investment accounts and none has a balance history */
const NO_INVESTMENT_HISTORY = "No balance recorded for the investment accounts yet. Their values over time appear here once one reports a balance.";

/** brokerage account values over time (from Balances); with none in the history, the card's frame and what is missing
 *  (with no investment account, the short form when the holdings card above says it in full: `short`) */
function InvestmentBalancesCard({ short }: { short: boolean }) {
  const { data } = useData();
  const latest = useSnapshots().latest;
  const { series, accounts } = data.balances;
  const ids = useMemo(() => Object.keys(series)
    .filter((id) => series[id].length > 0 && accounts[id] !== undefined && isInvestmentAccount(accounts[id]))
    .sort((a, b) => (accounts[a]?.name || a).localeCompare(accounts[b]?.name || b)), [series, accounts]);
  // one account's history can reach far back before the others begin; starting at the second-earliest
  // series start avoids a long single-line lead-in
  const start = useMemo(() => {
    const starts = ids.map((id) => series[id][0]?.date).filter(Boolean).sort();
    return starts.length >= 2 ? starts[1] : undefined;
  }, [ids, series]);
  // the lines are built once: the chart draws them and the "view data" table lists the very same arrays
  const chart = useMemo(() => {
    const lines = balanceLines(series, accounts, ids, (p) => p.balance, data.accounts.map((a) => a.id));
    const table: ChartTable = { ...timeTable(lines), csvName: "investment-balances.csv", label: "investment balances over time" };
    return { option: balanceTimeOptionOf(lines, { xMin: start }), table };
  }, [series, accounts, ids, start, data.accounts]);
  if (!ids.length) {
    return (
      <Card testId="card-investment-balances" title="Investment balances over time">
        <EmptyState>
          {data.accounts.some(isInvestmentAccount) ? NO_INVESTMENT_HISTORY : short ? NO_INVESTMENT_ACCOUNT_YET : <NoInvestmentAccount />}
        </EmptyState>
      </Card>
    );
  }
  return (
    <Card testId="card-investment-balances" title="Investment balances over time"
      right={<span className="text-[10px] text-zinc-500">brokerage accounts — point-in-time account value
        {ids.some((id) => marginBreakdown(latest, id) !== null) && ", after the margin loan"}</span>}>
      <EChart option={chart.option} tableData={chart.table} height={300} />
      {onePointEach(series, ids) && <OneBalanceEach />}
      <BalanceSpacingNote series={series} ids={ids} />
    </Card>
  );
}

const DRIFT_TITLE = "Asset-class targets (% of net worth)";

/** Drift vs the asset-class targets: the household's OWN targets (the family's in-app ones, else `targets:` in
 *  assumptions.yaml), each a share of NET WORTH (every asset less every debt, as `docs/configuration.md` states the key):
 *  not the share of assets Home's allocation divides by. This is the page's one card that says how far a class is
 *  over or under and what to move. It is on the page for every household: with no target stated it says so and offers
 *  the editor, never a drift against targets nobody set. It names where its targets come from and what they add up to
 *  as a share of net worth, with no push toward 100 (on this base a household's classes add up to more than 100 and its
 *  debts take the rest), naming from the snapshot the classes with no target and the debts that make up the rest. A net
 *  worth at or below zero has no share to give: one line says so instead of flipping the drift's sign. */
function ClassDriftCard() {
  const { data, prefs } = useData();
  const [editing, setEditing] = useState(false);
  const targets = data.assumptions.targets;
  const cur = useSnapshots().latest;
  const keys = Object.keys(targets);
  if (editing) {
    return <TargetsEditor onDone={() => setEditing(false)} />;
  }
  const edit = <EditButton onClick={() => setEditing(true)}>{keys.length ? "Edit asset-class targets" : "Set asset-class targets"}</EditButton>;
  if (!keys.length) {
    return (
      <Card testId="card-class-drift" title={DRIFT_TITLE} right={edit}>
        <EmptyState>
          No asset-class targets are stated, so there is nothing to measure against: set them with “Set asset-class
          targets” <span className="text-zinc-500">(or <Code>targets:</Code> in <Code>assumptions.yaml</Code>)</span>.
        </EmptyState>
      </Card>
    );
  }
  // no snapshot: nothing to measure the targets against yet (the concentration line above says it in full)
  if (cur === null) {
    return <Card testId="card-class-drift" title={DRIFT_TITLE} right={edit}><NoBalances short /></Card>;
  }
  // a share of net worth needs a net worth above zero: at zero there is none, below it every share would flip its sign
  if (cur.total <= 0) {
    return (
      <Card testId="card-class-drift" title={DRIFT_TITLE} right={edit}>
        <EmptyState>With a net worth at or below zero, a share of it means nothing: there is no drift to measure against
          the targets.</EmptyState>
      </Card>
    );
  }
  // the in-app targets replace the configuration's whole (lib/prefs.ts mergeAssumptions): the card says which it uses
  const fromApp = Object.keys(prefs.doc.targets).length > 0;
  const sum = keys.reduce((s, k) => s + targets[k], 0);
  // The targets are shares of net worth over the targeted classes; what they leave (100 less their sum) is for what has
  // no target: the classes held with no target, less the debts (net worth counts them below zero). The line says what
  // those two are today, as shares of the same net worth, so the remainder reads as what it is.
  const untargeted = Object.entries(cur.by_class).filter(([k, v]) => v > 0 && !(k in targets));
  const untargetedPct = untargeted.reduce((s, [, v]) => s + v, 0) / cur.total;
  const debtPct = Object.values(cur.by_class).filter((v) => v < 0).reduce((s, v) => s + v, 0) / cur.total;
  const today = [...(untargeted.length ? [`the classes with no target (${untargeted.map(([k]) => k).join(", ")}) are `
    + `${fmtPct(untargetedPct)} of it`] : []),
    ...(debtPct < 0 ? [untargeted.length ? `the debts ${fmtPct(debtPct)}`
      : `every class held has a target and the debts are ${fmtPct(debtPct)} of it`] : [])];
  const rows = keys.map((k) => {
    const actualPct = (cur.by_class[k] ?? 0) / cur.total * 100;
    const drift = actualPct - targets[k];
    return { k, targetPct: targets[k], actualPct, drift, dollars: drift / 100 * cur.total };
  }).sort((a, b) => Math.abs(b.drift) - Math.abs(a.drift));
  return (
    <Card testId="card-class-drift" title={DRIFT_TITLE} right={edit}>
      <p data-testid="class-drift-source" className="mb-2 text-xs text-zinc-400">
        {fromApp ? "The household's own targets, saved in the app for the whole family"
          : <>The household's own targets, from <Code>targets:</Code> in <Code>assumptions.yaml</Code></>}
        {`. They add up to ${fmtPct(sum / 100, Number.isInteger(sum) ? 0 : 1)} of net worth`}
        {sum < 100 && `, leaving ${fmtPct((100 - sum) / 100, Number.isInteger(sum) ? 0 : 1)} for what has no target`}
        {today.length ? `: today ${today.join(" and ")}.` : "."}
      </p>
      <TableScroll label="Drift by class table" stickyFirst>
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-zinc-700 text-left text-zinc-500">
            <th className="py-1">Class</th>
            <th className="text-right">Target</th>
            <th className="text-right">Actual</th>
            <th className="text-right">Drift</th>
            <th className="text-right">≈ Rebalance</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.k} className="border-b border-zinc-800/60">
              <td className="py-1">
                <span className="mr-2 inline-block h-2 w-2 rounded-full"
                  style={{ background: CLASS_COLORS[r.k] ?? "#9ca3af" }} />
                {r.k}
              </td>
              <td className="text-right tabular-nums">{r.targetPct}%</td>
              <td className="text-right tabular-nums">{r.actualPct.toFixed(1)}%</td>
              <td className={`text-right tabular-nums ${Math.abs(r.drift) > 3 ? "text-amber-400" : "text-zinc-400"}`}>
                {fmtPoints(r.drift)}
              </td>
              <td className="text-right tabular-nums">
                {r.drift > 0 ? `sell ${fmtUSD0(r.dollars)}` : `buy ${fmtUSD0(-r.dollars)}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </TableScroll>
      <p className="mt-2 text-[10px] text-faint">
        % of net worth ({fmtUSD0(cur.total)}, snapshot {fmtDate(cur.snapshot_date)}): everything owned less every debt,
        not the share of assets Home's allocation shows. Rebalance
        dollars are directional — mind taxes and where new contributions can do the work
        instead of selling.
      </p>
    </Card>
  );
}

/** Edit asset-class targets (% of net worth) — saved in-app to the family
 *  prefs store; drives ClassDriftCard immediately. Offered from that card. Targets saved here replace the configuration's
 *  whole, so while only the configuration states some the editor says so. */
function TargetsEditor({ onDone }: { onDone: () => void }) {
  const { data, prefs } = useData();
  const configOnly = Object.keys(prefs.doc.targets).length === 0 && Object.keys(data.assumptions.targets).length > 0;
  // the newest snapshot, for the share each class holds now; null with none (no "now" figure then)
  const cur = useSnapshots().latest;
  // offer every class the family actually holds (positive value), plus any
  // class that already has a target — debt classes aren't allocation targets
  const classes = useMemo(() => {
    const held = Object.entries(cur?.by_class ?? {})
      .filter(([, v]) => v > 0).map(([k]) => k);
    return Array.from(new Set([...held, ...Object.keys(prefs.doc.targets)])).sort();
  }, [cur, prefs.doc.targets]);
  const totalPct = Object.values(prefs.doc.targets).reduce((s, v) => s + v, 0);
  return (
    <Card testId="card-class-drift" title="Edit asset-class targets (% of net worth)"
      right={<DoneButton onClick={onDone} />}>
      <p className="mb-2 text-[11px] text-zinc-500">
        Blank = no target for that class. Saved for the whole family, instantly.
        {configOnly && <> The targets in use come from <Code>assumptions.yaml</Code>; the first one saved here replaces
          them all.</>}
        {totalPct > 0 && (
          <span data-testid="class-targets-sum" className="ml-1 text-zinc-500">
            These add up to {fmtPct(totalPct / 100, Number.isInteger(totalPct) ? 0 : 1)} of net worth. Targets are shares of
            net worth, so they need not add up to 100: what they leave is for the classes with no target, less the debts.
          </span>
        )}
        {prefs.localOnly && <LocalOnlyNotice what="targets" refused={prefs.refused} />}
      </p>
      <div className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
        {classes.map((k) => {
          const set = prefs.doc.targets[k];
          // the class's share now: none with no snapshot or a net worth at or below 0
          const actualPct = cur === null || cur.total <= 0 ? null : (cur.by_class[k] ?? 0) / cur.total * 100;
          return (
            <PrefEditorRow key={k} label={k} labelTitle={k} inputCls="w-20"
              min={0} max={100} step={1}
              value={set ?? ""}
              placeholder="—"
              onChange={(v) => prefs.setTarget(k, v)}
              after={
                <>
                  <span className="text-faint">%</span>
                  {actualPct !== null && <span className="text-[10px] text-faint">now {actualPct.toFixed(1)}%</span>}
                </>
              }
              onClear={set !== undefined ? () => prefs.setTarget(k, null) : undefined}
              clearTitle="clear target" />
          );
        })}
      </div>
    </Card>
  );
}

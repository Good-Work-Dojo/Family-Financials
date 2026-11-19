// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo } from "react";
import { Link } from "react-router-dom";
import { ACCOUNT_LINK } from "../components/AccountLink";
import BalanceSpacingNote from "../components/BalanceSpacingNote";
import EChart from "../components/EChart";
import { ManualAssetsLink } from "../components/ManualAssetsLink";
import { Code, EmptyState, NO_BALANCES_YET, NoBalances, NOT_ENOUGH_HISTORY, NotYetReported, OneBalanceEach } from "../components/NoData";
import RequireData from "../components/RequireData";
import { AsOfBadge, Card, PHONE_WRAP, StatCard, TableScroll } from "../components/ui";
import { useData } from "../data/DataContext";
import { useSnapshots } from "../data/useSnapshots";
import type { AssetRow, BalancePoint, BalancesTimeseries, Snapshot } from "../data/types";
import { isCashLike } from "../lib/accounts";
import { balanceTimeOption, onePointEach } from "../lib/balanceCharts";
import { historySince, lookbackRows } from "../lib/balanceLookback";
import { fmtUtilization, utilization } from "../lib/credit";
import { countOf, fmtDate, fmtDelta, fmtPct, fmtUSD0 } from "../lib/format";
import { balanceSheet, cardAccountIds, debtLineLabel, inCreditLine, liquidCash, liquidCashRows, type Liquidity, liquidity,
  marginBreakdown, netWorthDebt, netWorthDebtNote, rowHolder } from "../lib/lifeSummary";
import { CLASS_COLORS, DEBT_CLASSES } from "../lib/tokens";
import { useExpanded } from "../lib/useExpanded";

type AcctLike = { kind: string; asset_class: string };

// logical groupings for the current-balances table, in display order
// (assets first, then liabilities). Cash is the Cash class (isCashLike) —
// a brokerage's cash account is kind "brokerage" but its money is cash.
// Any account matching no section falls into a catch-all "Other" section
// so nothing silently disappears.
const BALANCE_SECTIONS: { key: string; label: string; match: (a: AcctLike) => boolean }[] = [
  { key: "cash", label: "Cash", match: (a) => isCashLike(a) },
  { key: "invest", label: "Investments", match: (a) => a.kind === "brokerage" && !isCashLike(a) },
  { key: "cards", label: "Credit cards", match: (a) => a.kind === "credit-card" },
  { key: "loans", label: "Loans", match: (a) => a.kind === "loan" },
];

/** per-account facts the KPI row and the tables both read; empty when balances_timeseries.json
 *  is missing or holds no history (DataContext hands over an empty series then) */
function useBalanceFacts({ series, accounts }: BalancesTimeseries) {
  const ids = useMemo(
    () => Object.keys(series).filter((id) => series[id].length > 0)
      .sort((a, b) => (accounts[a]?.name || a).localeCompare(accounts[b]?.name || b)),
    [series, accounts],
  );
  const latest = useMemo(() => {
    const m = new Map<string, BalancePoint>();
    for (const id of ids) m.set(id, series[id][series[id].length - 1]);
    return m;
  }, [ids, series]);
  // one bank-feed point anywhere is live history; the amber "CSV anchors only" badge shows only without one
  const hasLiveFeed = useMemo(
    () => Object.values(series).some((pts) => pts.some((p) => p.source === "simplefin")),
    [series],
  );
  const cashIds = useMemo(() => ids.filter((id) => accounts[id] && isCashLike(accounts[id])), [ids, accounts]);
  const asOf = useMemo(
    () => [...latest.values()].map((p) => p.date).sort().slice(-1)[0] || "—", [latest]);
  return { series, accounts, ids, latest, hasLiveFeed, cashIds, asOf };
}
type BalanceFacts = ReturnType<typeof useBalanceFacts>;

/** Accounts → Balances: where the money sits right now — cash and illiquid headline numbers, the
 *  grouped current-balances table with a cash history, and the balance sheet and liquidity cards
 *  (from the snapshot, so they never wait on the balance history). The investment lines live on
 *  Investments and the credit-card lines on Liabilities. */
export default function Balances() {
  const { data } = useData();
  // the newest snapshot; null for a household with none yet, whose snapshot cards say so
  const current = useSnapshots().latest;
  // Illiquid is the snapshot's asset rows that are not liquid (lib/lifeSummary.ts liquidity), never net worth less the
  // liquid part: debts are their own line, and liquid + illiquid - debts = net worth
  const liq = current === null ? null : liquidity(current);
  const illiquidRows = current === null ? [] : current.rows.filter((r) => !r.liquid && r.value > 0)
    .sort((a, b) => b.value - a.value);
  // what the illiquid rows are, by class; a debt class's row above zero (a card in credit) is an asset named for its
  // account, as the Assets card names it, never by its debt class
  const illiquidClasses = [...new Set(illiquidRows.map((r) =>
    (DEBT_CLASSES.has(r.asset_class) ? inCreditLine(r) : r.asset_class.toLowerCase())))];
  const facts = useBalanceFacts(data.balances);
  const hasHistory = facts.ids.length > 0;
  // "Cash on hand" is the app's one cash figure (liquidCash: the snapshot's liquid Cash rows, what Planning's runway
  // and Second House read). The table below lists the accounts with a balance history, so a liquid cash row outside it is
  // in the headline but not in the table, and the sub-line says why in its own words: a value entered by hand has no
  // bank feed at all; an account's row with no balance history has a feed that has not recorded one yet. It is the net of
  // the cash accounts, so an account below zero is taken off it (the balance sheet lists that account as a debt) and the
  // sub-line says so
  const cash = liquidCash(current);
  const cashRows = liquidCashRows(current);
  const byHand = cashRows.filter((r) => r.account_id === null);
  const noHistory = cashRows.filter((r) => r.account_id !== null && !facts.ids.includes(r.account_id));
  const overdrawn = cashRows.filter((r) => r.value < 0).length;
  const cashSub = [countOf(cashRows.length, "cash account"),
    overdrawn > 0 && `net of ${overdrawn === 1 ? "one" : overdrawn} below zero`,
    byHand.length > 0 && `${byHand.length} entered by hand (no bank feed), so not in the table below`,
    noHistory.length > 0 && `${noHistory.length} ${noHistory.length === 1 ? "has" : "have"} no balance history yet, so not in the table below`,
  ].filter(Boolean).join(" · ");
  return (
    <div className="space-y-4">
      {hasHistory && !facts.hasLiveFeed && (
        <div className="flex justify-end">
          <span className="rounded-full bg-amber-900/50 px-2 py-0.5 text-[10px] text-amber-300"
            title="Each balance here is one a statement file states. A bank feed records every connected account's balance at each pull, which builds a daily history.">
            Balances from statement files only — a bank feed adds a daily history
          </span>
        </div>
      )}

      {/* Illiquid reads the asset snapshot, so it renders whatever the balance history is doing; with no snapshot the
          snapshot tiles read "—" and say so in short (the balance-sheet card below says it in full) */}
      <div data-testid="kpis-balances" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {hasHistory && (cash === null
          ? <StatCard label="Cash on hand" value="—" sub={NO_BALANCES_YET} />
          : <StatCard label="Cash on hand" value={fmtUSD0(cash)} tone="pos" sub={cashSub} />)}
        {/* the classes it holds, named from its rows: no claim of a house or retirement the snapshot does not hold */}
        {liq === null
          ? <StatCard testId="kpi-illiquid" label="Illiquid assets" value="—" sub={NO_BALANCES_YET} />
          : <StatCard testId="kpi-illiquid" label="Illiquid assets" value={fmtUSD0(liq.illiquid)}
              sub={illiquidClasses.length === 0 ? "No illiquid asset in this snapshot"
                : `${illiquidClasses.join(", ")} — not spendable soon; debts are not taken off`} />}
        {hasHistory && <StatCard label="Accounts tracked" value={String(facts.ids.length)} sub={`as of ${fmtDate(facts.asOf)}`} />}
      </div>

      <RequireData files={["balances_timeseries.json"]} what="Balances">
        <BalanceTables facts={facts} current={current} />
      </RequireData>

      {current === null || liq === null ? (
        <div className="grid gap-3 lg:grid-cols-3">
          <Card testId="card-balance-sheet-assets" title="Assets"><NoBalances /></Card>
          <Card testId="card-balance-sheet-liabilities" title="Liabilities & net"><NoBalances short /></Card>
          <Card testId="card-liquidity" title="Liquidity"><NoBalances short /></Card>
        </div>
      ) : (
        <BalanceSheetCards current={current} liq={liq} illiquidRows={illiquidRows} />
      )}
    </div>
  );
}

/** What a row of the current-balances table says beside its balance, the desktop table and the phone cards alike: the
 *  date of a balance older than the date the card's badge shows (`asOf`, the newest balance in the table: "as of
 *  <date>", drawn amber; a balance of that date needs no date of its own), a margin account's balance named for what it
 *  is (the feed's balance, net of the margin loan; its positions are its holdings' classes and Investments' figure), and
 *  a card's utilisation (lib/credit.ts). A loan has no available credit and no utilisation: the feed's "available" on a
 *  loan is its balance again. */
export interface RowNotes { stale: boolean; netOfMargin: boolean; available: number | null; utilization: number | null }
export function rowNotes(p: BalancePoint, kind: string | undefined, asOf: string, netOfMargin: boolean): RowNotes {
  const debt = kind === "loan";
  return {
    stale: p.date < asOf, netOfMargin,
    available: debt || !p.available ? null : p.available,
    utilization: kind === "credit-card" ? utilization(p.balance, p.limit) : null,
  };
}

/** the current-balances table (desktop) / stacked cards (phones) and the cash history. A balance older than the date the
 *  card's badge shows says so; `current` is the newest snapshot (null with none), which names a margin account's row net
 *  of its loan. */
function BalanceTables({ facts, current }: { facts: BalanceFacts; current: Snapshot | null }) {
  const { data } = useData();
  const { series, accounts, ids, latest, asOf } = facts;
  const notesOf = (id: string) => rowNotes(latest.get(id)!, accounts[id]?.kind, asOf, marginBreakdown(current, id) !== null);
  // per-account performance expansion (multi-open: comparing accounts is the
  // natural use). Chevron only when a series has ≥2 points to compare.
  const { isOpen, toggle } = useExpanded("multi");
  const expandable = (id: string) => (series[id]?.length ?? 0) >= 2;

  // group the current-balance rows into labeled sections with subtotals. The cards' subtotal is what they owe: a card in
  // credit owes nothing and is an asset of its own on the balance sheet below, so its credit is never netted against
  // another card's debt; the section says how many are in credit.
  const sections = useMemo(() => {
    const used = new Set<string>();
    const balanceOf = (id: string) => latest.get(id)?.balance ?? 0;
    const built = BALANCE_SECTIONS.map((sec) => {
      const secIds = ids.filter((id) => accounts[id] && sec.match(accounts[id]));
      secIds.forEach((id) => used.add(id));
      const cards = sec.key === "cards";
      return {
        key: sec.key, label: sec.label, ids: secIds,
        subtotal: secIds.reduce((s, id) => s + (cards ? Math.min(0, balanceOf(id)) : balanceOf(id)), 0),
        inCredit: cards ? secIds.filter((id) => balanceOf(id) > 0).length : 0,
        // a live margin account's balance is net of its loan, so the subtotal is too (Investments counts positions gross)
        netOfMargin: secIds.some((id) => marginBreakdown(current, id) !== null),
      };
    });
    const leftover = ids.filter((id) => !used.has(id));
    if (leftover.length)
      built.push({
        key: "other", label: "Other", ids: leftover,
        subtotal: leftover.reduce((s, id) => s + balanceOf(id), 0), inCredit: 0, netOfMargin: false,
      });
    return built.filter((s) => s.ids.length > 0);
  }, [ids, accounts, latest, current]);
  /** a section's word on its cards in credit, beside its count: they are not in its subtotal */
  const inCreditNote = (n: number) => `${countOf(n, "in credit", "in credit")}, not in the subtotal`;
  /** a section's word on a margin account in it: its balance, and so the subtotal, is after the loan */
  const NET_OF_MARGIN = "after the margin loan";

  // balances are signed as the institution reports them — tint debts red
  const balEl = (v: number) =>
    <span className={v < 0 ? "text-red-400" : "text-zinc-200"}>{fmtUSD0(v)}</span>;

  // with no balance at all: what starts the history, and to a household with no bank feed what setting one up takes, in
  // words (the commands are the README's)
  if (ids.length === 0) {
    return (
      <div className="space-y-4">
        <Card testId="card-current-balances" title="No balance history yet">
          <div className="space-y-2 text-sm text-zinc-400">
            <p>
              It starts with the first ingest that reads a balance: a pull of the bank feed, or a statement file that
              states one.
            </p>
            {data.manifest.setup.feed ? (
              <p>The bank feed is set up; its balances appear after the next refresh.</p>
            ) : (
              <>
                <p>A bank feed adds a daily history, and any credit card's balance and utilization. To set one up:</p>
                <ol data-testid="feed-setup-steps" className="ml-4 list-decimal space-y-1">
                  <li>Claim the connection once, with the setup token SimpleFIN gives you.</li>
                  <li>Fetch the last 90 days, then map each account to its feed id (<Code>simplefin_id:</Code> in{" "}
                    <Code>accounts.yaml</Code>).</li>
                  <li>Refresh regularly: each refresh records every connected account's balance.</li>
                </ol>
                <p>The exact commands are in the README, under “The bank feed (SimpleFIN)”.</p>
              </>
            )}
          </div>
        </Card>
        <CashBalancesCard facts={facts} />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Card testId="card-current-balances" title="Current balances"
        right={<AsOfBadge date={asOf} generatedAt={data.manifest.generated_at} />}>
        {/* desktop: full table */}
        <TableScroll className="hidden md:block" label="Current balances table">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-zinc-500">
              <tr>
                <th className="w-8 px-2 py-1.5" aria-label="expand" />
                <th className="px-2 py-1.5">Account</th>
                <th className="px-2 py-1.5">Owner</th>
                <th className="px-2 py-1.5 text-right">Balance</th>
                <th className="px-2 py-1.5 text-right">Available</th>
                <th className="px-2 py-1.5 text-right">Limit</th>
                <th className="px-2 py-1.5 text-right">Utilization</th>
              </tr>
            </thead>
            {sections.map((sec) => (
              <tbody key={sec.key}>
                <tr className="border-t border-zinc-700 bg-zinc-900/50">
                  <td colSpan={3} className="px-2 py-1.5 text-xs font-semibold uppercase tracking-wide text-zinc-300">
                    {sec.label}
                    <span className="ml-2 font-normal normal-case text-zinc-500">
                      {countOf(sec.ids.length, "account")}
                      {sec.inCredit > 0 && ` · ${inCreditNote(sec.inCredit)}`}
                      {sec.netOfMargin && ` · ${NET_OF_MARGIN}`}
                    </span>
                  </td>
                  <td className="px-2 py-1.5 text-right font-semibold tabular-nums">{balEl(sec.subtotal)}</td>
                  <td colSpan={3} />
                </tr>
                {sec.ids.map((id) => {
                  const p = latest.get(id)!;
                  const n = notesOf(id);
                  const canExpand = expandable(id);
                  const open = isOpen(id);
                  return [
                    <tr key={id}
                      onClick={canExpand ? () => toggle(id) : undefined}
                      aria-expanded={canExpand ? open : undefined}
                      className={`border-t border-zinc-800/60 hover:bg-zinc-900/40 ${
                        canExpand ? "cursor-pointer" : ""}`}>
                      <td className="px-2 py-1.5 text-center">
                        {canExpand && (
                          <span aria-hidden
                            className={`inline-block text-zinc-500 transition-transform ${
                              open ? "rotate-90" : ""}`}>›</span>
                        )}
                      </td>
                      <td className="px-2 py-1.5 text-zinc-200">
                        {/* the row itself toggles the history; the name goes to the account's page and must not */}
                        <Link to={`/accounts/${id}`} onClick={(e) => e.stopPropagation()}
                          className={ACCOUNT_LINK}>{accounts[id]?.name || id}</Link>
                        {n.netOfMargin && <span className="ml-2 text-[11px] text-zinc-500">net of the margin loan</span>}
                        {n.stale && <span className="ml-2 text-[11px] text-amber-300">as of {fmtDate(p.date)}</span>}
                      </td>
                      <td className="px-2 py-1.5 text-zinc-500">{accounts[id]?.owner || "—"}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{balEl(p.balance)}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums text-zinc-400">
                        {n.available === null ? "—" : fmtUSD0(n.available)}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums text-zinc-400">
                        {p.limit != null ? fmtUSD0(p.limit) : "—"}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{fmtUtilization(n.utilization)}</td>
                    </tr>,
                    open ? (
                      <tr key={`${id}-perf`} className="bg-zinc-900/30">
                        <td colSpan={7} className="px-4 py-2">
                          <LookbackPanel points={series[id]} />
                        </td>
                      </tr>
                    ) : null,
                  ];
                })}
              </tbody>
            ))}
          </table>
        </TableScroll>
        {/* phones: stacked account cards — no sideways scrolling */}
        <div className="space-y-4 md:hidden">
          {sections.map((sec) => (
            <div key={sec.key}>
              <div className="mb-1.5 flex items-baseline justify-between px-1">
                <span className="text-xs font-semibold uppercase tracking-wide text-zinc-300">
                  {sec.label}
                  {sec.inCredit > 0 && (
                    <span className="ml-2 font-normal normal-case tracking-normal text-zinc-500">{inCreditNote(sec.inCredit)}</span>
                  )}
                  {sec.netOfMargin && (
                    <span className="ml-2 font-normal normal-case tracking-normal text-zinc-500">{NET_OF_MARGIN}</span>
                  )}
                </span>
                <span className="text-sm font-semibold tabular-nums">{balEl(sec.subtotal)}</span>
              </div>
              <div className="divide-y divide-zinc-800 rounded-lg border border-zinc-800">
                {sec.ids.map((id) => {
                  const p = latest.get(id)!;
                  const n = notesOf(id);
                  const canExpand = expandable(id);
                  const open = isOpen(id);
                  const extras = [
                    n.netOfMargin ? "net of the margin loan" : null,
                    n.available === null ? null : `avail ${fmtUSD0(n.available)}`,
                    p.limit != null ? `limit ${fmtUSD0(p.limit)}` : null,
                    n.utilization === null ? null : `${fmtUtilization(n.utilization)} used`,
                  ].filter(Boolean);
                  const name = accounts[id]?.name || id;
                  const amount = balEl(p.balance);
                  return (
                    <div key={id} className="p-3">
                      {/* a link inside a button is invalid HTML, so the name (the account's page), the amount (plain
                          text: a button's aria-label would replace it for a screen reader) and the chevron-only
                          history toggle are three siblings, each 44px tall */}
                      <div className="flex items-center justify-between gap-2">
                        <Link to={`/accounts/${id}`} className="flex min-h-[44px] min-w-0 grow items-center text-sm">
                          <span className={`${PHONE_WRAP} ${ACCOUNT_LINK}`}>{name}</span>
                        </Link>
                        <span className="shrink-0 text-sm tabular-nums">{amount}</span>
                        {canExpand ? (
                          <button type="button" onClick={() => toggle(id)}
                            aria-expanded={open} aria-controls={`perf-${id}`}
                            aria-label={`${open ? "Hide" : "Show"} balance history for ${name}`}
                            className="flex min-h-[44px] min-w-[44px] shrink-0 items-center justify-center text-zinc-500">
                            <span aria-hidden className={`transition-transform ${open ? "rotate-90" : ""}`}>›</span>
                          </button>
                        ) : (
                          <span aria-hidden className="min-w-[44px] shrink-0" />
                        )}
                      </div>
                      {/* a current balance carries no date of its own (the card's badge dates the table); an older one
                          says "as of" its date, in amber, as the desktop table does */}
                      <div className="text-left text-[11px] text-zinc-500">
                        {[accounts[id]?.owner || null, ...extras].filter(Boolean).join(" · ")}
                        {n.stale && <span className="text-amber-300">
                          {accounts[id]?.owner || extras.length > 0 ? " · " : ""}as of {fmtDate(p.date)}</span>}
                      </div>
                      {open && (
                        <div id={`perf-${id}`} className="mt-2">
                          <LookbackPanel points={series[id]} />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </Card>

      <CashBalancesCard facts={facts} />
    </div>
  );
}

/** the cash card's line when the balance history holds no cash account: the household has none (what to add, and the
 *  key as a hint), or none has reported */
const NoCashAccount = () => (
  <>
    No cash account yet. Add a checking or savings account and its balance over time appears here after the next ingest
    (<Code>kind: checking</Code> or <Code>kind: savings</Code> in <Code>accounts.yaml</Code>).
  </>
);
const NO_CASH_HISTORY = "No balance recorded for the cash accounts yet. Their balance over time appears here once one reports a balance.";

/** the cash accounts' balances over time; with no cash account in the history, its frame and what is missing; with one
 *  balance for each, the points and a line under them saying so */
function CashBalancesCard({ facts }: { facts: BalanceFacts }) {
  const { data } = useData();
  const { series, accounts, cashIds } = facts;
  return (
    <Card testId="card-cash-balances" title="Cash balances over time">
      {cashIds.length > 0 ? <>
        <EChart option={balanceTimeOption(series, accounts, cashIds, (p) => p.balance, data.accounts.map((a) => a.id))} height={300} />
        {onePointEach(series, cashIds) && <OneBalanceEach />}
        <BalanceSpacingNote series={series} ids={cashIds} />
      </> : <EmptyState>{data.accounts.some(isCashLike) ? NO_CASH_HISTORY : <NoCashAccount />}</EmptyState>}
    </Card>
  );
}

/** how many illiquid rows the Liquidity card lists; the rest are one line with their count and sum */
const ILLIQUID_LISTED = 8;

/** Balance sheet (assets | liabilities & net) and liquidity — all from the latest asset snapshot. `liq` is its split
 *  (lib/lifeSummary.ts liquidity) and `illiquidRows` the asset rows that are not liquid, largest first: the rows
 *  Illiquid sums. */
function BalanceSheetCards({ current, liq, illiquidRows }: { current: Snapshot; liq: Liquidity; illiquidRows: AssetRow[] }) {
  const { data } = useData();
  // the Mortgage line is the one mortgage balance (the same as Liabilities and Planning -> House); this is the latest snapshot
  const cardIds = useMemo(() => cardAccountIds(data.accounts), [data.accounts]);
  const debt = useMemo(() => netWorthDebt(data.assumptions, current, true, cardIds), [data.assumptions, cardIds, current]);
  const bs = useMemo(() => balanceSheet(current, debt.mortgage > 0 ? debt.mortgage : undefined), [current, debt]);
  // the Cash CLASS counts every Cash row, an escrow balance included; "Cash on hand" above counts liquid cash only
  const nonLiquidCash = current.rows.filter((r) => r.asset_class === "Cash" && !r.liquid).length;
  const asOf = <AsOfBadge date={current.snapshot_date} generatedAt={data.manifest.generated_at} />;
  return (
    <div className="grid gap-3 lg:grid-cols-3">
      <Card testId="card-balance-sheet-assets" title="Assets" right={asOf}>
        <SheetTable lines={bs.assets} total={bs.totalAssets} totalLabel="Total assets" none="No asset above zero in this snapshot." />
        {nonLiquidCash > 0 && (
          <p className="mt-2 text-[10px] text-faint">
            Cash here is the whole Cash class, including {nonLiquidCash === 1 ? "a row" : `${nonLiquidCash} rows`} that cannot be spent (an escrow balance, say);
            "Cash on hand" above counts liquid cash only.
          </p>
        )}
      </Card>
      <Card testId="card-balance-sheet-liabilities" title="Liabilities & net" right={asOf}>
        <SheetTable lines={bs.liabilities} total={bs.totalLiabilities} totalLabel="Total liabilities" neg
          none="No debt in this snapshot." label={(name) => debtLineLabel(current, name, cardIds)} />
        <div className="mt-3 flex justify-between border-t border-zinc-700 pt-2 text-sm font-semibold">
          <span>Net worth</span>
          <span className="tabular-nums text-emerald-400">{fmtUSD0(bs.net)}</span>
        </div>
        <p data-testid="liabilities-note" className="mt-2 text-[10px] text-faint">
          Total liabilities = the debt in net worth: {netWorthDebtNote(debt, data.assumptions.net_worth.include_credit_cards,
            cardIds.size > 0)}.{" "}
          {/* the card balances only where the household has a card */}
          <a href="#/accounts/liabilities" className="text-blue-400 hover:underline">
            {cardIds.size > 0 ? "Liabilities → credit cards" : "Liabilities →"}
          </a>
        </p>
        <p className="mt-1 text-[10px] text-faint">
          net = assets − liabilities, by sign: every row above zero is an asset, every row below zero a liability (an
          account below zero is its own line)
        </p>
      </Card>
      <Card testId="card-liquidity" title="Liquidity" right={asOf}>
        <div className="space-y-3 pt-2">
          {/* the bar splits the assets (its two parts add up to them, never past 100%): with none above zero there is
              nothing to split */}
          {liq.assets <= 0 ? (
            <p className="text-xs text-zinc-500">With no asset above zero there is no share to draw.</p>
          ) : (
            <div className="h-7 w-full overflow-hidden rounded-lg bg-zinc-800">
              <div className="flex h-full">
                <div className="bg-emerald-600" style={{ width: `${(liq.liquid / liq.assets) * 100}%` }} />
                <div className="bg-zinc-600" style={{ width: `${(liq.illiquid / liq.assets) * 100}%` }} />
              </div>
            </div>
          )}
          <div data-testid="liquidity-split" className="flex flex-wrap justify-between gap-x-3 text-xs">
            <span className="text-emerald-400">Liquid {fmtUSD0(liq.liquid)}</span>
            <span className="text-zinc-400">Illiquid {fmtUSD0(liq.illiquid)}</span>
            <span className={liq.debts > 0 ? "text-red-300" : "text-zinc-400"}>Debts {fmtUSD0(liq.debts)}</span>
          </div>
          {/* the card prints the debts too: while the snapshot misses any account, an asset's or a debt's, its figures
              are of the balances reported so far, and it says so */}
          {current.missing.length > 0 && (
            <p className="text-xs text-zinc-400">Figures <NotYetReported count={current.missing.length} of="balances" /></p>
          )}
          <ul data-testid="liquidity-rows" className="space-y-1 text-xs text-zinc-400">
            {illiquidRows.slice(0, ILLIQUID_LISTED).map((r) => (
              <li key={`${r.account_id ?? r.manual_id ?? ""}|${r.account}|${r.owner}`} className="flex justify-between gap-2">
                {/* a value entered by hand links to where it is entered; a value older than the snapshot says its date;
                    a card in credit is named as the Assets card names it, never by its debt class */}
                <span className="min-w-0">
                  {DEBT_CLASSES.has(r.asset_class) ? <>{inCreditLine(r)} <span className="text-faint">· {rowHolder(r)}</span></>
                    : <>{r.manual_id !== null ? <ManualAssetsLink>{r.account}</ManualAssetsLink> : r.account}{" "}
                      <span className="text-faint">({r.asset_class} · {rowHolder(r)})</span></>}
                  {r.as_of !== null && r.as_of < current.snapshot_date && (
                    <span className="text-amber-300"> as of {fmtDate(r.as_of)}</span>
                  )}
                </span>
                <span className="shrink-0 tabular-nums">{fmtUSD0(r.value)}</span>
              </li>
            ))}
            {illiquidRows.length > ILLIQUID_LISTED && (
              <li className="flex justify-between gap-2">
                <span>{countOf(illiquidRows.length - ILLIQUID_LISTED, "more asset")}</span>
                <span className="shrink-0 tabular-nums">
                  {fmtUSD0(illiquidRows.slice(ILLIQUID_LISTED).reduce((s, r) => s + r.value, 0))}</span>
              </li>
            )}
          </ul>
          <p className="text-[10px] text-faint">
            Illiquid is the assets that cannot be spent soon: retirement accounts (a 10% penalty before 59½), a home, a
            vehicle, anything with no market to sell into. Debts are their own line: liquid + illiquid − debts = net
            worth.
          </p>
        </div>
      </Card>
    </div>
  );
}

/** one side of the balance sheet; with no line on it (`none` says what is absent) its total is no $0 but "—" */
function SheetTable({ lines, total, totalLabel, neg, none, label = (name) => name }: {
  lines: { name: string; value: number }[]; total: number; totalLabel: string; neg?: boolean; none: string;
  /** what a line is called on the page (its name is the class, which keys its colour) */
  label?: (name: string) => string;
}) {
  return (
    <table className="w-full text-xs">
      <tbody>
        {lines.length === 0 && (
          <tr className="border-b border-zinc-800/60"><td colSpan={2} className="py-1.5 text-zinc-500">{none}</td></tr>
        )}
        {lines.map((l) => (
          <tr key={l.name} className="border-b border-zinc-800/60">
            <td className="py-1.5">
              <span className="mr-2 inline-block h-2 w-2 rounded-full"
                style={{ background: CLASS_COLORS[l.name] ?? "#9ca3af" }} />
              {label(l.name)}
            </td>
            <td className={`text-right tabular-nums ${neg ? "text-red-300" : ""}`}>
              {fmtUSD0(neg ? -l.value : l.value)}
            </td>
          </tr>
        ))}
        <tr>
          <td className="py-1.5 font-semibold">{totalLabel}</td>
          <td className={`text-right font-semibold tabular-nums ${neg && lines.length > 0 ? "text-red-300" : ""}`}>
            {lines.length === 0 ? "—" : fmtUSD0(neg ? -total : total)}
          </td>
        </tr>
      </tbody>
    </table>
  );
}

/** Expanded row: balance change vs 1w/1mo/3mo/6mo/1y ago. The history-since
 *  line explains why longer lookbacks are absent while SimpleFIN history is
 *  still weeks-deep; each row shows its real anchor date so a tolerated
 *  overshoot never misleads. Green Δ = net worth improved (works for cards
 *  and loans too — they're stored negative, so paydown is positive). */
function LookbackPanel({ points }: { points: BalancePoint[] }) {
  const rows = lookbackRows(points);
  const hist = historySince(points);
  const pctTxt = (pct: number | null) => {
    if (pct === null) return "—";
    if (Math.abs(pct) > 9.99) return pct > 0 ? ">+999%" : "<−999%";
    return `${pct >= 0 ? "+" : ""}${fmtPct(pct, 1)}`;
  };
  return (
    <div className="text-xs">
      {hist && (
        <div className="mb-1.5 text-[10px] text-zinc-500">
          history since {fmtDate(hist.since)} · {hist.count} observation{hist.count === 1 ? "" : "s"}
          {rows.length < 5 && " — longer lookbacks appear as history accumulates"}
        </div>
      )}
      {rows.length === 0 ? (
        <div className="text-zinc-500">{NOT_ENOUGH_HISTORY} for a comparison</div>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-x-4 gap-y-1.5">
          {rows.map((r) => (
            <div key={r.label} className="flex items-baseline gap-2">
              <span className="w-8 shrink-0 font-medium text-zinc-400">{r.label}</span>
              <span className={`tabular-nums ${
                r.delta > 0 ? "text-emerald-400" : r.delta < 0 ? "text-red-400" : "text-zinc-400"}`}>
                {fmtDelta(r.delta)}
              </span>
              <span className={`text-[10px] tabular-nums ${
                (r.pct ?? 0) > 0 ? "text-emerald-500/80" : (r.pct ?? 0) < 0 ? "text-red-500/80" : "text-zinc-500"}`}>
                {pctTxt(r.pct)}
              </span>
              <span className="ml-auto text-[10px] text-faint">since {r.anchorDate.slice(5)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import BalanceSpacingNote from "../components/BalanceSpacingNote";
import EChart from "../components/EChart";
import GatedLink from "../components/GatedLink";
import { Code, OneBalanceEach } from "../components/NoData";
import RequireData from "../components/RequireData";
import TxnTable from "../components/TxnTable";
import { AsOfBadge, Badge, Card, Line } from "../components/ui";
import { useData } from "../data/DataContext";
import { useSnapshots } from "../data/useSnapshots";
import type { AccountMeta } from "../data/types";
import { applyFilters, EMPTY_FILTERS } from "../filters/filters";
import { dataThrough, datedAhead } from "../lib/agg";
import { balanceTimeOption, onePointEach } from "../lib/balanceCharts";
import { checkText } from "../lib/checkLabels";
import { countOf, fmtDate, fmtUSD, fmtUSD0, humanizeDates } from "../lib/format";
import { marginBreakdown } from "../lib/lifeSummary";
import { maskMoneyText } from "../lib/privacy";
import { runningBalance, runningBalanceEligible } from "../lib/runningBalance";
import { purchaseCount } from "../lib/splits";

const STATUS_CLS: Record<string, string> = {
  pass: "bg-emerald-950 text-emerald-300", info: "bg-zinc-800 text-zinc-400",
  warn: "bg-amber-950 text-amber-300", fail: "bg-red-950 text-red-300",
};
/** a check's status in words, after its label on the chip (the status code itself is the ETL's) */
const STATUS_WORD: Record<string, string> = { pass: "passed", info: "for information", warn: "needs a look", fail: "failed" };

/** the quality checks that name this account: reconcile.<id>.…, simplefin.….<id>, coverage.<id> */
export function checksFor<T extends { name: string }>(checks: readonly T[], id: string): T[] {
  return checks.filter((c) => c.name.includes(`.${id}.`) || c.name.endsWith(`.${id}`));
}

/** /accounts/:id — header + feed status, balance history, ledger drift, the register with a running balance */
export default function AccountPage() {
  const { id = "" } = useParams();
  return (
    <RequireData files={["accounts.json"]} what="This account">
      <AccountBody id={id} />
    </RequireData>
  );
}

function AccountBody({ id }: { id: string }) {
  const { data, missing } = useData();
  const acct = data.accounts.find((a) => a.id === id);
  const rows = useMemo(() => (acct
    ? applyFilters(data.transactions, { ...EMPTY_FILTERS, accounts: [acct.id], includeInternal: true }) : []), [data.transactions, acct]);
  // the card counts transactions, as the register's CSV button names them: a split purchase is one however many parts
  // `rows` holds for it
  const purchases = useMemo(() => purchaseCount(rows), [rows]);
  const series = useMemo(() => data.balances.series[id] ?? [], [data.balances.series, id]);
  const running = useMemo(() => (acct ? runningBalance(rows, series, acct) : null), [acct, rows, series]);
  const checks = useMemo(() => checksFor(data.quality.checks, id), [data.quality.checks, id]);
  // the account's rows the bank dated after the ingest day: real rows, listed (and marked) in the register below, but no
  // day of data, so "Transactions through" stops at the ingest day and a line names them, as Data does for the household
  const ahead = useMemo(() => rows.filter((t) => datedAhead(t.date, data.manifest.generated_at)), [rows, data.manifest.generated_at]);
  // a live margin account has two figures, each named for what it is: the feed's balance, net of the margin loan (what
  // Balances lists), and its positions (what Investments values it at)
  const margin = marginBreakdown(useSnapshots().latest, id);
  if (!acct) return <UnknownAccount id={id} />;
  const through = acct.txn_data_end === null ? null : dataThrough(acct.txn_data_end, data.manifest.generated_at);
  const latestAhead = ahead.reduce<string | null>((m, t) => (m === null || t.date > m ? t.date : m), null);
  return (
    <div className="space-y-4">
      <Card testId="card-account-header" title={acct.name}
        right={<AsOfBadge date={acct.latest_balance_as_of} generatedAt={data.manifest.generated_at} />}>
        <ul className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
          <Line k="Institution" v={acct.institution} />
          <Line k="Owner" v={acct.owner} />
          <Line k="Kind" v={acct.kind} />
          <Line k={margin === null ? "Latest balance" : "Balance, net of the margin loan"}
            v={acct.latest_balance === null ? "—" : fmtUSD0(acct.latest_balance)} />
          {margin !== null && <>
            <Line k="Positions" v={fmtUSD0(margin.positions)} />
            <Line k="Margin loan" v={fmtUSD0(margin.loan)} />
          </>}
          <Line k="Transactions from" v={acct.txn_data_start ? fmtDate(acct.txn_data_start) : "—"} />
          <Line k="Transactions through" v={through ? fmtDate(through) : "—"} />
          {latestAhead !== null && (
            <Line k="Dated ahead of that" v={`${countOf(ahead.length, "row")}, latest ${fmtDate(latestAhead)}`} />
          )}
        </ul>
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
          {/* a household with no bank feed has no feed status: its checks are of the files */}
          <span className="text-zinc-400">{data.manifest.setup.feed ? "Feed status:" : "Checks:"}</span>
          {missing.has("quality_report.json")
            ? <span className="text-zinc-400">unavailable — the quality report could not be loaded</span>
            : checks.length === 0
              ? <span className="text-zinc-400">no checks name this account</span>
              : checks.map((c, i) => (
                // the label is words; the check's own id and what it found ride in the tooltip. A check name repeats when
                // the ETL runs a check once per statement file, so a chip's key is its place too
                <Badge key={`${i}:${c.name}`} cls={STATUS_CLS[c.status] ?? STATUS_CLS.info} title={`${c.name} — ${humanizeDates(maskMoneyText(c.detail))}`}>
                  {checkText(c.name, [{ id, name: acct.name }], data.manifest.setup.feed).label}: {STATUS_WORD[c.status] ?? c.status}
                </Badge>))}
        </div>
        <p className="mt-2 text-[10px] text-faint"><Link to="/accounts" className="text-blue-400 hover:underline">← Balances</Link></p>
      </Card>
      <div className="grid gap-4 lg:grid-cols-3">
        {/* two thirds of the row; the wrapper (not the card) spans, so the missing-data card takes the same room */}
        <div className="min-w-0 lg:col-span-2">
          <RequireData files={["balances_timeseries.json"]} what="Balance history">
            <Card testId="card-account-balance-history"
              title={margin === null ? "Balance history" : "Balance history — net of the margin loan"}>
              {series.length === 0
                ? <p className="text-xs text-zinc-500">No balance for this account yet. One appears after an ingest that reads a balance for it: from the bank feed, or a statement file that states one.</p>
                : <>
                  <EChart option={balanceTimeOption(data.balances.series, data.balances.accounts, [id], (p) => p.balance,
                    data.accounts.map((a) => a.id))} height={260} />
                  <BalanceSpacingNote series={data.balances.series} ids={[id]} />
                  {onePointEach(data.balances.series, [id]) && <OneBalanceEach single />}
                </>}
            </Card>
          </RequireData>
        </div>
        <LedgerDriftCard acct={acct} />
      </div>
      <Card testId="card-account-transactions" title={`Transactions — ${countOf(purchases, "transaction")}, transfers included`}>
        <TxnTable txns={rows} runningBalance={running ?? undefined} hideAccount />
        <p className="mt-2 text-[10px] text-faint">
          {running
            ? `End-of-day balance, shown on each day's row nearest the later days: worked back from the account's latest balance through the posted rows${acct.ledger_drift ? "; pending charges shift it (see the ledger-drift card)" : ""}.`
            : !runningBalanceEligible(acct)
              ? `No running balance: a ${acct.kind} balance moves with the market, not with posted rows.`
              : missing.has("balances_timeseries.json")
                ? "No running balance: the balance history could not be loaded, so there is no balance to anchor on."
                : "No running balance: this account has no balance to anchor on yet."}
        </p>
      </Card>
    </div>
  );
}

/** the page in place of an account's for an id no account has: the header card says so and offers the way back. Its
 *  `unknown-account` line is what the route matrix reads as a page replaced, with no other card to look for. */
export function UnknownAccount({ id }: { id: string }) {
  return (
    <Card testId="card-account-header" title="Unknown account">
      <p data-testid="unknown-account" className="text-xs text-zinc-400">
        This household has no account with the id <Code>{id}</Code>: it may have been renamed or removed. Balances lists
        the accounts that have reported a balance.{" "}
        <GatedLink to="/accounts" className="text-blue-400 hover:underline">Go to Balances →</GatedLink>
      </p>
    </Card>
  );
}

function LedgerDriftCard({ acct }: { acct: AccountMeta }) {
  const d = acct.ledger_drift;
  return (
    <Card testId="card-ledger-drift" title="Ledger vs feed balance">
      {!d ? (
        <p className="text-xs text-zinc-500">
          No ledger-drift check for this account: it needs a checking, savings or credit-card account connected to the
          bank feed, with two balances at least 7 days apart (an investment account has none: its balance moves with the
          market).
        </p>
      ) : (
        <>
          <ul className="space-y-1 pt-1 text-xs">
            <Line k="Window" v={`${fmtDate(d.from)} → ${fmtDate(d.to)}`} />
            {/* cents: a drift of a few cents must not read "$0" */}
            <Line k="Feed balance moved" v={fmtUSD(d.expected)} />
            <Line k="Posted rows sum to" v={fmtUSD(d.got)} />
            <Line k="Drift" v={fmtUSD(d.drift)} />
          </ul>
          <p className="mt-2 text-[10px] text-faint">
            For information only: the bank feed's balance includes pending charges the ingest leaves out, so the drift
            is about the pending total on most days — the check itself is on the{" "}
            <Link to="/data" className="text-blue-400 hover:underline">quality report</Link>.
          </p>
        </>
      )}
    </Card>
  );
}

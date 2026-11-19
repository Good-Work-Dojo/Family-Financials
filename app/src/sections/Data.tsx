// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { Fragment, useId, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { RefreshPanel } from "../components/DataRefresh";
import LearnedRulesList from "../components/LearnedRulesList";
import AccountLink, { ACCOUNT_LINK } from "../components/AccountLink";
import { ManualAssetsLink } from "../components/ManualAssetsLink";
import { Code, EmptyState, NO_BALANCES_YET, NO_TRANSACTIONS_YET } from "../components/NoData";
import RequireData, { MissingDataCard } from "../components/RequireData";
import { Card, Line, PHONE_WRAP, TableScroll } from "../components/ui";
import { useData } from "../data/DataContext";
import type { AccountMeta } from "../data/types";
import { useSnapshots } from "../data/useSnapshots";
import { freshnessEnd, freshnessRows } from "../lib/accountFreshness";
import { coverageFloor, coverageFloorLabel, monthInProgress, prevMonth } from "../lib/agg";
import { isReviewCandidate, uncategorizedSpend } from "../lib/answers";
import { checkLabel } from "../lib/checkLabels";
import { type LeftOutMonthEnd, leftOutMonthEnds } from "../lib/comparableSnapshots";
import { firstMortgageBalance, mortgageGap } from "../lib/decomposition";
import { firstRunState } from "../lib/firstRun";
import { countOf, fmtCompact, fmtDate, fmtMonth, fmtUSD0, humanizeDates } from "../lib/format";
import { type IngestSource, ingestSources } from "../lib/ingestRead";
import { homeValue, snapshotsCarrying } from "../lib/lifeSummary";
import { payrollConfigured } from "../lib/payroll";
import { maskMoneyText } from "../lib/privacy";
import { SCROLL_TARGETS, useScrollToCardParam } from "../lib/scrollTargets";
import { splitRules } from "../lib/splits";
import { useVocab } from "../lib/useVocab";
import PrivacyCard from "./data/PrivacyCard";
import ProjectionDefaultsCard from "./data/ProjectionDefaultsCard";
import SavedViewsCard from "./data/SavedViewsCard";

/** Data: how fresh the numbers are (and the refresh button), what the ingest flagged, what is
 *  still unresolved, what the ETL learned, the assumptions every projection runs on, and the family's
 *  saved views. A `?card=` link lands on the privacy, projection-defaults, saved-views, caveats or "what the last ingest
 *  read" card (lib/scrollTargets.ts). */
export default function Data() {
  const { data, indexes, missing } = useData();
  useScrollToCardParam();
  // the newest transaction date can be TOMORROW (a pending charge's expected settlement is a future-dated row): the
  // page says the data runs through the ingest day at most (Indexes.dataThrough), and counts what is dated beyond it,
  // naming the newest row's own day as the latest of them (null: no transaction)
  const newest = indexes.dataRange?.last ?? null;
  const maxDate = indexes.dataThrough;
  const later = useMemo(() => (maxDate === null ? 0 : data.transactions.reduce((n, t) => (t.date > maxDate ? n + 1 : n), 0)),
    [data.transactions, maxDate]);
  // the newest snapshot; null for a household with none yet
  const current = useSnapshots().latest;
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">Data</h1>
      {/* items-start: the refresh card is short and the per-account list is 24 rows tall; stretched to match, it was half empty */}
      <div className="grid items-start gap-4 lg:grid-cols-2">
        <Card testId="card-freshness" title="Freshness & refresh">
          <RefreshPanel />
          <ul className="mt-3 space-y-1 border-t border-zinc-800 pt-3 text-xs">
            <Line k="Transactions through" v={maxDate === null ? NO_TRANSACTIONS_YET : fmtDate(maxDate)} />
            {/* an account below can end on that later date (its newest row is a pending charge's expected settlement): the
                list says so here instead of leaving one date past the headline unexplained */}
            {later > 0 && newest !== null && <Line k="Dated ahead of that" v={`${countOf(later, "row")}, latest ${fmtDate(newest)}`} />}
            <Line k="Asset snapshot" v={current === null ? NO_BALANCES_YET : fmtDate(current.snapshot_date)} />
          </ul>
        </Card>
        <RequireData files={["accounts.json"]} what="Data freshness by account">
          <AccountFreshnessCard />
        </RequireData>
      </div>
      <RequireData files={["quality_report.json"]} what="The quality report">
        <QualityReportCard />
      </RequireData>
      <RequireData files={["quality_report.json"]} what="What the last ingest read">
        <IngestReadCard />
      </RequireData>
      <RequireData files={["open_questions.json"]} what="Open questions">
        <OpenQuestionsCard />
      </RequireData>
      <Card testId="card-learned-rules" title="Learned rules">
        {missing.has("vendors.json")
          ? <MissingDataCard compact files={["vendors.json"]} what="The learned-rules list" />
          : <div className="text-xs"><LearnedRulesList vendors={data.vendors} /></div>}
      </Card>
      <SplitRulesCard />
      <div className="grid gap-4 lg:grid-cols-2">
        <AssumptionsInForceCard />
        <ProjectionDefaultsCard />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <SavedViewsCard />
        <PrivacyCard />
      </div>
      <CaveatsCard />
    </div>
  );
}

/** per-account `txn_data_end`, amber when it stops before the last full month (the month before the month in progress,
 *  lib/agg.ts monthInProgress) - those rows first (none is amber with no month in progress: there is no last full month
 *  to lag behind) */
export function AccountFreshnessCard() {
  const { data } = useData();
  const inProgress = useMemo(() => monthInProgress(data), [data]);
  const behind = inProgress === null ? null : `${prevMonth(inProgress)}-01`;
  const rows = useMemo(() => freshnessRows(data.accounts, behind), [data.accounts, behind]);
  const stale = rows.filter((r) => r.stale).length;
  // no account at all: one line (what to add, the file as a hint), no empty list and no legend for it
  if (rows.length === 0) {
    return (
      <Card testId="card-account-freshness" title="Data freshness by account">
        <EmptyState>
          No account yet. Add one and the day its transactions reach appears here after the next ingest
          (<Code>accounts.yaml</Code>).
        </EmptyState>
      </Card>
    );
  }
  return (
    <Card testId="card-account-freshness" title="Data freshness by account">
      {/* no nested scroll box at any width: a capped list hid the stale row below its fold with no cue (and on
          a phone the rows are 40px tap targets); the amber rows lead instead */}
      <ul className="space-y-1 pt-1 text-xs">
        {rows.map(({ account: a, stale: late }) => (
          <li key={a.id} className="flex items-center justify-between gap-2">
            {/* a 40px row on a phone: the account links are tap targets */}
            <Link to={`/accounts/${a.id}`} className={`flex min-h-[40px] min-w-0 items-center md:min-h-0 ${PHONE_WRAP}`}>
              <span className={`${PHONE_WRAP} ${ACCOUNT_LINK}`}>{a.name}</span>
            </Link>
            <span className={`shrink-0 tabular-nums ${late ? "text-amber-400" : "text-zinc-500"}`}>
              {freshnessEnd(a)}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[10px] text-faint">
        amber = transactions stop before the last full month{stale > 0 ? ` (${stale} listed first)` : " — no account lags"}
      </p>
    </Card>
  );
}

export function QualityReportCard() {
  const { data } = useData();
  const flagged = data.quality.checks.filter((c) => c.status === "warn" || c.status === "fail");
  // an ingest that read no transaction and no balance gave its checks nothing to look at: no claim that they passed
  const latest = useSnapshots().latest;
  const readNothing = data.transactions.length === 0 && latest === null;
  // a check is shown by its words (and the account it is about); its code rides in the tooltip
  const label = (name: string) => checkLabel(name, data.accounts, data.manifest.setup.feed);
  return (
    <Card testId="card-quality-report" title={`Quality report — ${countOf(flagged.length, "warning")} of ${countOf(data.quality.checks.length, "check")}`}>
      {flagged.length === 0 && (readNothing
        ? <EmptyState>Nothing to check yet: the last ingest read no transaction and no balance.</EmptyState>
        : <p className="text-xs text-zinc-500">every check passed on the last ingest</p>)}
      <div className="space-y-2 text-xs">
        {/* a check name repeats when the ETL runs a check once per statement file, so a row's key is its place too */}
        {flagged.map((c, i) => (
          <div key={`${i}:${c.name}`} className={`rounded border p-2 ${c.status === "fail" ? "border-red-900/60 bg-red-950/30" : "border-amber-900/60 bg-amber-950/30"}`}>
            <span title={c.name} className={`font-medium ${c.status === "fail" ? "text-red-400" : "text-amber-400"}`}>{label(c.name)}</span>
            <p className="text-zinc-400">{humanizeDates(maskMoneyText(c.detail))}</p>
          </div>
        ))}
      </div>
      <details className="mt-2 text-xs">
        <summary className="cursor-pointer select-none text-zinc-400">all {countOf(data.quality.checks.length, "check")}</summary>
        <ul className="mt-1 space-y-0.5 text-[11px]">
          {data.quality.checks.map((c, i) => (
            <li key={`${i}:${c.name}`} className="flex justify-between gap-2">
              <span title={c.name} className={`text-zinc-300 ${PHONE_WRAP}`}>{label(c.name)}</span>
              <span className={c.status === "pass" ? "text-emerald-500" : c.status === "info" ? "text-zinc-500" : "text-amber-400"}>{c.status}</span>
            </li>
          ))}
        </ul>
      </details>
    </Card>
  );
}

/** the sources shown before the rest wait behind a button (what read nothing is always shown) */
const SOURCES_SHOWN = 12;
/** what a source that yielded no row says in place of its count */
const NO_ROWS: Record<Exclude<IngestSource["result"], "read">, string> = {
  "nothing read": "nothing read", "empty": "nothing in it", "no file": "no file found",
};

/** What the last ingest read, file by file (lib/ingestRead.ts): each statement file by its own name - the household's,
 *  so it is shown - with the rows it yielded and the lines it skipped and why, the bank feed as one source over all its
 *  pulls, and each account no file was found for. When every source read rows the card is one summary line, the table
 *  behind a disclosure: nothing there needs action. When one read nothing, the table is open, and what read nothing
 *  leads, in amber; past SOURCES_SHOWN the sources that read rows wait behind a button. */
export function IngestReadCard() {
  const { data } = useData();
  const sources = useMemo(() => ingestSources(data.quality, data.accounts), [data.quality, data.accounts]);
  const [all, setAll] = useState(false);
  const [open, setOpen] = useState(false);
  const tableId = useId();
  const quiet = sources.filter((s) => s.result !== "read");
  // nothing came back empty: one line, and the table only on request
  const summary = sources.length > 0 && quiet.length === 0;
  const capped = !summary && sources.length > Math.max(SOURCES_SHOWN, quiet.length);
  const shown = all || !capped ? sources
    : [...quiet, ...sources.filter((s) => s.result === "read").slice(0, SOURCES_SHOWN - quiet.length)];
  // the table is in the page whenever there is a source (the disclosure's target), hidden while the summary stands in
  const table = !summary || open;
  const feed = sources.some((s) => s.kind === "feed");
  return (
    <Card testId="card-ingest-read" id={SCROLL_TARGETS.ingestRead} title="What the last ingest read">
      {sources.length === 0 && (
        <EmptyState>The last ingest read no statement file and no bank-feed pull.</EmptyState>
      )}
      {summary && (
        <p data-testid="ingest-read-summary" className="flex flex-wrap items-center gap-x-2 text-xs text-zinc-300">
          <span>{countOf(sources.length, "source")} read; none came back empty.</span>
          <button type="button" aria-expanded={open} aria-controls={tableId} onClick={() => setOpen(!open)}
            className="inline-flex min-h-[40px] items-center text-blue-400 hover:underline md:min-h-0">
            {open ? "Hide the sources" : "Show the sources"}
          </button>
        </p>
      )}
      {sources.length > 0 && <div id={tableId} hidden={!table}>
        <TableScroll label="Sources table">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-zinc-700 text-left text-zinc-500">
                <th scope="col" className="py-1 pr-2 font-medium">File or source</th>
                <th scope="col" className="py-1 pr-2 text-right font-medium">Rows read</th>
                <th scope="col" className="py-1 text-right font-medium">Skipped</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((s, i) => <IngestRow key={`${i}:${s.kind}:${s.name ?? ""}`} s={s} />)}
            </tbody>
          </table>
        </TableScroll>
        {capped && (
          <button type="button" aria-expanded={all} onClick={() => setAll(!all)}
            className="mt-1 inline-flex min-h-[40px] items-center text-xs text-blue-400 hover:underline md:min-h-0">
            {all ? "Show fewer" : `Show all ${sources.length} sources`}
          </button>
        )}
        <p className="mt-2 text-[10px] text-faint">
          Rows read are the transactions a file or a pull held; a skipped line of a statement file holds none (a header,
          a total, a blank line) or could not be read.
          {feed && " The bank feed's pulls overlap, so a transaction two pulls carried is counted in each here and kept once; its skipped rows are pending charges and rows the statement files cover."}
          {quiet.some((s) => s.kind === "file") && <> A file that reads nothing is often another bank's export or another
            account's: each account reads its files in one format (<Code>parser:</Code> in <Code>accounts.yaml</Code>).</>}
          {quiet.some((s) => s.kind === "no-file") && <> An account with no file found looks for its statement files by the
            pattern shown (<Code>glob:</Code> in <Code>accounts.yaml</Code>).</>}
        </p>
      </div>}
    </Card>
  );
}

/** one source's row: amber when it read nothing */
function IngestRow({ s }: { s: IngestSource }) {
  const name = s.kind === "feed" ? `Bank feed · ${countOf(s.pulls, "pull")}` : maskMoneyText(s.name ?? "");
  return (
    <tr className={`border-b border-zinc-800/50 align-top ${s.result === "read" ? "text-zinc-300" : "text-amber-300"}`}>
      <th scope="row" className="py-1 pr-2 text-left font-normal [overflow-wrap:anywhere]">
        {s.account !== null ? <AccountLink id={s.account}>{name}</AccountLink> : name}
        {s.hint !== null && <> — looks for <Code>{s.hint}</Code></>}
        {s.repeats > 0 && (
          <span className="block text-[10px] text-faint">{countOf(s.repeats, "row")} another file already held, kept once</span>
        )}
      </th>
      <td className="whitespace-nowrap py-1 pr-2 text-right tabular-nums">
        {s.result === "read" ? countOf(s.read, "row") : NO_ROWS[s.result]}
      </td>
      <td className="py-1 text-right tabular-nums">
        {s.kind === "no-file" ? "—" : s.skipped === 0 ? "none" : <>
          {countOf(s.skipped, s.kind === "feed" ? "row" : "line")}
          <span className="block text-[10px] text-faint">{s.reasons.join(" · ")}</span>
        </>}
      </td>
    </tr>
  );
}

/** the questions, data gaps and uncategorized line, plus the to-review count */
export function OpenQuestionsCard() {
  const { data } = useData();
  const vocab = useVocab();
  // the sidebar badge's own rule (answers already applied), so the two numbers cannot disagree
  const review = data.transactions.filter((t) => isReviewCandidate(vocab, t)).length;
  const uncat = useMemo(() => uncategorizedSpend(vocab, data.transactions), [vocab, data.transactions]);
  return (
    <Card testId="card-open-questions" title={`Open questions — ${data.questions.length}`}>
      <p className="mb-2 text-xs text-zinc-500">
        {countOf(review, "transaction")} to review (<Link to="/review" className="text-blue-400 hover:underline">Review</Link>)
        · {countOf(data.dataGaps.length, "data gap")}
      </p>
      {/* a question's topic is the ETL's own slug (the transfer rule's name): an id for debugging, kept on the row, never its text */}
      <ol className="list-decimal space-y-1.5 pl-5 text-xs text-zinc-300">
        {data.questions.map((q, i) => (
          <li key={i} data-topic={q.topic}>
            {humanizeDates(maskMoneyText(q.question))}
            {q.context && <span className="text-zinc-500"> — {humanizeDates(maskMoneyText(q.context))}</span>}
          </li>
        ))}
      </ol>
      {uncat.count > 0 && (
        <p className="mt-2 text-xs text-zinc-400">
          <span className="font-medium text-zinc-200">Uncategorized spending:</span>{" "}
          {countOf(uncat.count, "transaction")} · {fmtUSD0(uncat.total)} sit in {vocab.cashMisc} without a real category —{" "}
          <Link className="text-blue-400 underline" to="/review">review the rows</Link>
        </p>
      )}
      {data.dataGaps.length > 0 && (
        <details className="mt-2 text-xs text-zinc-400">
          <summary className="cursor-pointer select-none text-zinc-300">
            Data gaps — {countOf(data.dataGaps.length, "transfer")} between the household's own accounts with only one side in
            the data (kept out of spending; no money is missing)
          </summary>
          <ul className="mt-1.5 list-disc space-y-1 pl-5">
            {data.dataGaps.map((q, i) => (
              <li key={i}>{humanizeDates(maskMoneyText(q.question))}{q.context && <span className="text-zinc-500"> — {humanizeDates(maskMoneyText(q.context))}</span>}</li>
            ))}
          </ul>
        </details>
      )}
    </Card>
  );
}

/** The vendor rules that are splitting purchases right now (parity with the learned rules above), read off the split
 *  structures themselves - each leg's share is computed from the parts, so it is what the pages really contain. No amounts.
 *  Absent when nothing is split. */
function SplitRulesCard() {
  const { data, indexes } = useData();
  const rules = useMemo(() => splitRules(data.splitParents, indexes.childrenByParent), [data.splitParents, indexes.childrenByParent]);
  if (!rules.length) return null;
  return (
    <Card testId="card-split-rules" title={`Split rules in force — ${rules.length}`}>
      <ul className="space-y-1.5 text-xs text-zinc-300">
        {rules.map((r) => (
          <li key={r.vendor}>
            <span className="font-medium text-zinc-200">{r.vendor}</span>
            {" — "}{countOf(r.purchases, "transaction")} split:{" "}
            {r.legs.map((l) => `${Math.round(l.share * 100)}% ${l.category}${l.subcategory ? ` / ${l.subcategory}` : ""}`).join(" · ")}
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[10px] text-faint">
        Each comes from a vendor rule that splits the vendor's purchases (<Code>split:</Code> in{" "}
        <Code>vendor_rules.yaml</Code>). A split purchase is recorded once, at its full amount; its parts carry the spend,
        so these shares sit inside the category totals, the budget grid, the income statement and the essentials basis.
        Take the rule out and the next ingest puts the purchases back whole.
      </p>
    </Card>
  );
}

/** the seed assumptions every projection runs on, plus the family's in-app overrides and where to edit them */
export function AssumptionsInForceCard() {
  const { data, prefs } = useData();
  const p = data.assumptions.projections;
  const d = prefs.doc;
  // asset-class targets have no in-app way to start (their card shows only while some are set): named only when there are some
  const classTargets = Object.keys(d.targets).length;
  // the pages that edit budgets, payroll and the target mix show the household's data only once it has some: before
  // that the first-run page stands in for them, so they are not pointed at
  const pagesOpen = firstRunState(data.manifest, data) === null;
  // the home's value is no assumption: the current snapshot's House-class rows (homeValue), listed here because Planning ->
  // House's equity starts from it, and left out when the snapshot holds none or there is no snapshot (that page says what
  // to add)
  const home = homeValue(useSnapshots().latest);
  return (
    <Card testId="card-assumptions-in-force" title="Assumptions in force">
      <ul className="space-y-1 pt-1 text-xs">
        <Line k="Expected return scenarios" v={p.return_scenarios_pct.map((r) => `${r}%`).join(" / ")} />
        <Line k="Inflation" v={`${p.inflation_pct}%`} />
        <Line k="LTCG / state" v={`${p.ltcg_pct}% / ${p.state_income_tax_pct}%`} />
        <Line k="Withdrawal rate" v={`${p.withdrawal_rate_pct}%`} />
        {home !== null && <Line k="Home value (current snapshot)" v={fmtUSD0(home)} />}
        <Line k="In-app overrides" v={`${countOf(Object.keys(d.budgets).length, "budget")} · target mix ${d.target_mix ? "saved" : "not saved"} · ${classTargets > 0 ? `${countOf(classTargets, "asset-class target")} · ` : ""}${countOf(Object.keys(d.projections).length, "projection default")} · ${countOf(d.payroll.phases.length, "payroll phase")}`} />
      </ul>
      <p className="mt-2 text-[10px] text-faint">
        These start from the household's assumptions file; after changing it, re-run the ingest
        (<Code>assumptions.yaml</Code>).{pagesOpen ? <>{" "}Budgets and payroll:{" "}
        <Link to="/cash-flow/budget" className="text-blue-400 hover:underline">Cash Flow → Budget</Link> · target mix:{" "}
        <Link to="/accounts/investments" className="text-blue-400 hover:underline">Accounts → Investments</Link> · projection
        defaults: right here →</> : " Projection defaults: right here →"}
      </p>
    </Card>
  );
}

/** at most this many account names a line, then how many more */
const NAMES_SHOWN = 5;
/** past this many month ends the list of those the history leaves out sits behind a count */
const LEFT_OUT_OPEN = 6;

/** Account names as the app prints them: each linking its page where the registry has an account of that name (a
 *  snapshot's `missing` holds display names, which are unique), at most NAMES_SHOWN, then how many more. */
function AccountNames({ names, accounts }: { names: readonly string[]; accounts: readonly AccountMeta[] }) {
  const ids = new Map(accounts.map((a) => [a.name, a.id]));
  return (
    <>
      {names.slice(0, NAMES_SHOWN).map((n, i) => {
        const id = ids.get(n);
        return <Fragment key={n}>{i > 0 && ", "}{id === undefined ? n : <AccountLink id={id}>{n}</AccountLink>}</Fragment>;
      })}
      {names.length > NAMES_SHOWN && ` and ${names.length - NAMES_SHOWN} more`}
    </>
  );
}

/** the month ends the history leaves out, newest first, each with the accounts it has no balance for */
function LeftOutList({ months, accounts }: { months: readonly LeftOutMonthEnd[]; accounts: readonly AccountMeta[] }) {
  return (
    <ul className="mt-1 space-y-0.5 pl-3 text-zinc-500">
      {months.map((m) => (
        <li key={m.date}>
          {fmtDate(m.date)}: {m.misses.length === 0 ? "an account had not reported yet"
            : <>no balance yet for <AccountNames names={m.misses} accounts={accounts} /></>}
        </li>
      ))}
    </ul>
  );
}

/** the standing caveats the old report's appendix carried, plus the device-local note */
export function CaveatsCard() {
  const { data, prefs } = useData();
  const hasPayroll = payrollConfigured(prefs.doc.payroll.phases);
  const mortgageAtStart = firstMortgageBalance(data.assets.snapshots);
  const gap = mortgageGap(data.assets.snapshots, data.assumptions.mortgage?.schedule);
  const floorLabel = coverageFloorLabel(data.cashflow);
  // the floor is named only while some month of the data comes before it: on a household whose accounts all start
  // together (one account, say) nothing is left out, and there is nothing to say
  const floor = coverageFloor(data.cashflow);
  const staged = floor !== null && data.cashflow.some((m) => m.month < floor);
  // the month ends the loader left out of every page's history: before them, not every account was reporting yet. Each
  // is listed, newest first, with the accounts it has no balance for that the newest snapshot has
  const partial = data.partialSnapshots.length;
  const historyStart = data.assets.snapshots[0]?.snapshot_date;
  const latest = useSnapshots().latest;
  const leftOut = useMemo(() => leftOutMonthEnds(data.partialSnapshots, latest), [data.partialSnapshots, latest]);
  // the accounts the newest snapshot itself has no balance for: configured, their first balance not arrived yet
  const never = latest?.missing ?? [];
  const one = never.length === 1;
  // how many month ends hold a hand-stated value carried back (Home's history names each such asset)
  const carrying = snapshotsCarrying(data.assets.snapshots);
  return (
    <Card testId="card-caveats" id={SCROLL_TARGETS.caveats} title="Caveats that travel with every number">
      <ul className="list-disc space-y-1 pl-4 pt-1 text-xs text-zinc-400">
        {partial > 0 && historyStart && (
          <li data-testid="caveat-left-out">
            Net-worth history starts {fmtMonth(historyStart.slice(0, 7))}: {countOf(partial, "earlier month-end")} {partial === 1 ? "is" : "are"} left out because not every account was reporting yet.
            {leftOut.length > LEFT_OUT_OPEN ? (
              <details className="mt-1">
                <summary className="cursor-pointer select-none text-zinc-300">
                  Show the {countOf(leftOut.length, "month end")} and what each misses
                </summary>
                <LeftOutList months={leftOut} accounts={data.accounts} />
              </details>
            ) : <LeftOutList months={leftOut} accounts={data.accounts} />}
          </li>
        )}
        {never.length > 0 && (
          <li data-testid="caveat-never-reported">
            <AccountNames names={never} accounts={data.accounts} /> {one ? "is" : "are"} listed among the accounts because{" "}
            {one ? "it is" : "they are"} configured, but {one ? "its first balance has" : "their first balances have"} not
            arrived: net worth leaves {one ? "it" : "them"} out until an ingest reads one{one ? "" : " for each"} (a
            bank-feed pull, or a statement file that states a balance).
          </li>
        )}
        {gap && <li>Net-worth snapshots before {gap.sinceLabel} don't subtract the mortgage{mortgageAtStart === null ? "" : ` (~${fmtCompact(mortgageAtStart)})`} — cross-period growth comparisons carry a † for this reason.</li>}
        {staged && floorLabel !== null && <li data-testid="caveat-coverage">Rates and averages start in {floorLabel}, the first
          month by which enough of the accounts had their first transaction; the months before it are left out of them{" "}
          <span className="text-faint">(<Code>coverage.min_share</Code> in <Code>assumptions.yaml</Code>)</span>.</li>}
        {hasPayroll
          ? <li>Pre-tax 401k contributions come from the payroll facts entered in-app (Cash Flow → Budget) — the "total savings rate" there includes them; the cashflow savings rate remains take-home basis.</li>
          : <li data-testid="caveat-payroll">Deductions taken from pay before it reaches the bank, such as a 401(k), are not in the bank
            data: where there are some, true savings is higher than the cash-flow savings rate shows.
            {firstRunState(data.manifest, data) === null && " (Enter payroll facts under Cash Flow → Budget to count them.)"}</li>}
        <li data-testid="caveat-manual-assets">An asset entered by hand (a home, a private holding) is valued at the
          figure entered for it, as of the date entered; it moves only when that figure is updated, in{" "}
          <ManualAssetsLink /> <span className="text-faint">(or <Code>manual_entries:</Code> in{" "}
          <Code>accounts.yaml</Code>)</span>.
          {carrying > 0 && <> Before the first figure entered for it, the net-worth history counts it at that figure
            (from the day it was acquired, when one is stated): {countOf(carrying, "month end")} of the history{" "}
            {carrying === 1 ? "holds" : "hold"} a value stated later.</>}</li>
        <li>Projections are deterministic illustrations of stated assumptions, not forecasts.</li>
        <li>The emergency-fund "essential" toggles (Planning → Projections) are saved in this browser only: another device or
          browser does not see them.</li>
      </ul>
    </Card>
  );
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { RUN_RATE_MIN_MONTHS } from "../lib/agg";
import { notYetReported } from "../lib/comparableSnapshots";
import { countOf, fmtDate } from "../lib/format";
import { cardPath, SCROLL_TARGETS } from "../lib/scrollTargets";

// What a card says in place of a figure the household's data cannot give yet: what is missing and the one thing that
// brings it, in the same words on every page. A card that needs the newest net-worth snapshot and finds none shows
// <NoBalances />; one that needs transactions and finds none shows <NoTransactions />; a delta, a trend or a comparison
// whose newest snapshot is the only one shows <NotEnoughHistory>, never a change of 0; one that lacks something of its
// own (an account of a kind, a credit limit, a detected bill) says so in <EmptyState>. A page says the long line once:
// the first full-width card that lacks the data carries it and the cards after it the short one (<NoBalances short />).
// A KPI tile prints "—" with the short line only (a few words: the long one would wrap in a narrow tile and stretch
// its row); where no card below says the long line, a full-width line under the row does.

/** a card with no snapshot to read */
export const NO_BALANCES =
  "No balances yet. They appear after the first ingest that sees an account balance — from a bank feed, a statement file "
  + "with a balance, or an asset you enter by hand.";
/** the short form: a KPI tile, a line of a list, or a card after the one that carries NO_BALANCES. A phrase, not a
 *  sentence, so no full stop: a tile's line takes one only when it is a full sentence */
export const NO_BALANCES_YET = "No balances yet";
/** a card with no transaction to read */
export const NO_TRANSACTIONS =
  "No transactions yet. They appear after the first ingest that reads a statement file or a bank feed's transactions.";
/** the short form, as NO_BALANCES_YET */
export const NO_TRANSACTIONS_YET = "No transactions yet";
/** the one phrase a delta or a trend says while there is nothing earlier to compare the newest snapshot with */
export const NOT_ENOUGH_HISTORY = "Not enough history yet";
/** a savings-rate tile's line with no full month of cash flow at all: the same words on Home and on Cash Flow */
export const NO_FULL_MONTH = `${NOT_ENOUGH_HISTORY}: no full month of cash flow`;

/** the empty-state line's classes: at least 4.5:1 on a card in both themes (lib/contrast.test.ts) */
export const EMPTY_LINE = "text-xs text-zinc-400";

/** the cards' empty-state line */
function Line({ testId, children }: { testId: string; children: ReactNode }) {
  return <p data-testid={testId} className={EMPTY_LINE}>{children}</p>;
}

/** NO_BALANCES, or with `short` NO_BALANCES_YET: a card after the page's first that lacks a snapshot */
export function NoBalances({ short = false }: { short?: boolean }) {
  return <Line testId="no-balances">{short ? NO_BALANCES_YET : NO_BALANCES}</Line>;
}

/** NO_TRANSACTIONS, or with `short` NO_TRANSACTIONS_YET: a card after the page's first that lacks a transaction */
export function NoTransactions({ short = false }: { short?: boolean }) {
  return <Line testId="no-transactions">{short ? NO_TRANSACTIONS_YET : NO_TRANSACTIONS}</Line>;
}

/** NOT_ENOUGH_HISTORY, then what the card shows once there is (`children`) */
export function NotEnoughHistory({ children }: { children: ReactNode }) {
  return <Line testId="not-enough-history">{NOT_ENOUGH_HISTORY} — {children}</Line>;
}

/** How far a household's history has come, for the one line a planning page says while its run-rates wait for full
 *  months (lib/agg.ts RUN_RATE_MIN_MONTHS): the full months there are (`have`, through the count formatter, "0 full
 *  months" included) and the day the next one ends (`nextEnds`, lib/agg.ts nextFullMonthEnds, from the exports, never
 *  the clock); with no such day (the data stops short of the ingest month) the count grows once later transactions are
 *  read, and no date is named. */
export function historySoFar(have: number, nextEnds: string | null): string {
  return `So far: ${countOf(have, "full month")}; `
    + (nextEnds === null ? "the count grows once later transactions are read." : `the next one ends ${fmtDate(nextEnds)}.`);
}

/** the short line of a card whose figure waits for more full months than the household has: the page's own line says
 *  what it needs and how many there are (historySoFar), so the card says only this, and its tiles print "—" alone */
export function NotEnoughHistoryYet() {
  return <Line testId="not-enough-history">{NOT_ENOUGH_HISTORY}.</Line>;
}

/** what the planning run-rates need, in words: the line's middle (RUN_RATE_MIN_MONTHS full months of transactions) */
export const RUN_RATE_NEEDS = `${countOf(RUN_RATE_MIN_MONTHS, "full month")} of transactions`;

/** the one short line a tile whose figure rests on a planning run-rate says while it waits: what it needs and how many
 *  full months there are (`have`, lib/agg.ts runRateMonths), so a "—" never stands alone */
export function runRateWaits(have: number): string {
  return `${NOT_ENOUGH_HISTORY}: needs ${RUN_RATE_NEEDS}, ${countOf(have, "so far", "so far")}`;
}

/** under a balance chart whose every account has one balance so far (lib/balanceCharts.ts onePointEach): the chart draws
 *  the points, and this says the line comes with the second */
export function OneBalanceEach({ single = false }: { single?: boolean } = {}) {
  return (
    <NotEnoughHistory>
      {single ? "one balance so far" : "one balance so far for each account"}; the line draws once a second one is recorded.
    </NotEnoughHistory>
  );
}

/** The mark beside a figure of today's balances while the snapshot misses `count` accounts it would hold
 *  (lib/comparableSnapshots.ts `notYetReported`: "of the assets reported so far — 2 accounts not yet reported"), so a
 *  figure of an incomplete total is not read as the whole. It links to Data's caveats, which name the accounts. Nothing
 *  when the count is 0. */
export function NotYetReported({ count, of }: { count: number; of: "assets" | "balances" }) {
  const text = notYetReported(count, of);
  return text === null ? null : (
    <Link to={cardPath("/data", SCROLL_TARGETS.caveats)} data-testid="not-yet-reported"
      className="text-amber-300 underline decoration-amber-300/40 underline-offset-2 hover:decoration-amber-300">{text}</Link>
  );
}

/** a card's own empty state, in the same style: what the card lacks and the one thing that brings it, in plain words,
 *  then at most a hint naming the config key in <Code>. A card keeps its frame and heading and shows this line in
 *  place of its figures and chart. */
export function EmptyState({ children }: { children: ReactNode }) {
  return <Line testId="empty-state">{children}</Line>;
}

/** the code pill's classes: a dark wash with light text of its own in the dark theme (the line's own muted grey on the
 *  wash fell under 4.5:1); in the light theme a light background with dark text, which the dark wash over a white card
 *  would leave at about 1.6:1 (lib/contrast.test.ts holds both to 4.5:1) */
export const CODE_PILL = "rounded bg-black/40 px-1 text-zinc-300 light:bg-zinc-800 light:text-zinc-200";

/** a config key or file named in a line, in the pill the dashboard prints code in */
export function Code({ children }: { children: ReactNode }) {
  return <code className={CODE_PILL}>{children}</code>;
}

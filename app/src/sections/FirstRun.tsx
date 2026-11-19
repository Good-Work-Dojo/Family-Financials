// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { type ReactNode, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useRefreshSchedule } from "../components/DataRefresh";
import { ManualAssetsLink } from "../components/ManualAssetsLink";
import { Code } from "../components/NoData";
import { Card } from "../components/ui";
import { type LookOutcome, NOTHING_BUILT_RECHECK_MS, useData } from "../data/DataContext";
import type { Manifest, Setup } from "../data/types";
import { clock } from "../lib/clock";
import type { FirstRunPage } from "../lib/firstRun";
import { countOf } from "../lib/format";
import { nextRefreshSentence } from "../lib/refreshSchedule";
import { cardPath, SCROLL_TARGETS } from "../lib/scrollTargets";

// The first-run page, in three states. "nothing-built": no export exists yet (the loader found no manifest), so the
// app shows this page outside its shell, with all three steps to take. "setup": an ingest ran (so the household is
// described: it accepted household.yaml) and no account or hand-stated asset is listed. "waiting": something is listed
// and no data has arrived (lib/firstRun.ts firstRunState). Plain words first, each config key after them as a hint, and
// so is the set-up command (`ff init`) on the page shown before anything is built, the one place a command is the thing
// to do; no figure, no chart, and no editor or document the tree does not have. Inside the shell App.tsx pageRoute
// mounts it in place of a route's page, so the address stays and a bookmark opens its page once data arrives.

/** how often the page shown while nothing is built looks again, in words, from the interval itself
 *  (data/DataContext.tsx NOTHING_BUILT_RECHECK_MS) */
const RECHECK_WORDS = `every ${NOTHING_BUILT_RECHECK_MS / 1000} seconds`;

/** a look for the exports at once; it resolves to what it found (data/DataContext.tsx LookOutcome) */
type CheckAgain = () => Promise<LookOutcome>;

/** what the status line says after a manual look: still nothing, or a look that failed - the exports could not be reached
 *  (a network error) or not read (a 403, a 500, a file that does not parse). A look that loaded the data says nothing
 *  (the page is about to give way to the app). */
const LOOK_SAYS: Record<LookOutcome, string> = {
  "nothing-built": "Looked again: still nothing built.",
  failed: "Looked again: the data could not be reached or read; reload the page for details.",
  loaded: "",
};

/** the page's frame: the marker the route matrix and the smoke suite look for, its state, and one h1. `onCheckAgain`
 *  (nothing built only) loads the exports again at once; `quietLooks` counts the provider's own timed looks that found
 *  nothing built (one that does clears a failed manual look's line: the exports were reached since) */
export default function FirstRun({ state, onCheckAgain, quietLooks = 0 }: {
  state: FirstRunPage; onCheckAgain?: CheckAgain; quietLooks?: number;
}) {
  return (
    <div data-testid="first-run" data-first-run={state} className="mx-auto max-w-2xl space-y-4">
      {state === "waiting" ? <Waiting />
        : <SetUp built={state === "setup"} onCheckAgain={onCheckAgain} quietLooks={quietLooks} />}
    </div>
  );
}

/** the card that carries the page's heading and what the state means, in every state */
function Heading({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card testId="card-first-run">
      <h1 className="text-xl font-bold">{title}</h1>
      <p className="mt-2 text-sm text-zinc-300">{children}</p>
    </Card>
  );
}

/** one step: what to do in plain words, then the config keys it is written in (`hint`); a step already taken says so
 *  in words, after a check mark (`done`) */
function Step({ what, children, hint, done }: { what: string; children: ReactNode; hint: ReactNode; done?: string }) {
  return (
    <li className="pl-1">
      <span className="font-medium text-zinc-100">{what}</span>: {children} <span className="text-zinc-500">({hint})</span>
      {done && (
        <span className="mt-1 block text-xs font-medium text-emerald-400">
          <span aria-hidden="true">✓ </span>{done}
        </span>
      )}
    </li>
  );
}

/** which pages stay open meanwhile */
const STAND_IN = "this page stands in for every page but Data and Settings.";

/** Where a value no account reports can be entered right away, Settings staying open meanwhile: the Set-up and the
 *  Waiting cards say so. The page shown before anything is built does not (there is no store to save to yet). */
const EnterByHand = () => (
  <p data-testid="first-run-manual-assets" className="mt-3 text-sm text-zinc-300">
    A home, a vehicle or anything else no account reports can be entered now, in <ManualAssetsLink />.
  </p>
);

/** where an ingest is run from the dashboard, for a household with no bank feed once one has built its exports (Data
 *  stays open meanwhile): Data's "Rebuild from files" button. Before anything is built there is no dashboard to run it
 *  from, so the words stay "re-run the ingest". */
const RebuildOnData = () => (
  <>rebuild the dashboard with “Rebuild from files” on <Link to="/data" className="text-blue-400 hover:underline">Data</Link></>
);

/** where every key, every folder, the ingest, the statement formats and the bank feed's access are explained: the
 *  same sentence on the Set-up and the Waiting cards */
const DOCS_LINE = (
  <>
    Every key is described in <Code>docs/configuration.md</Code>; where each folder is, how to run the ingest, the
    statement formats and how to set up the bank feed's access are in <Code>README.md</Code>.
  </>
);

/** the three steps; with `built` (an ingest wrote exports) the first is done */
function SetUp({ built, onCheckAgain, quietLooks }: { built: boolean; onCheckAgain?: CheckAgain; quietLooks: number }) {
  // a manual look says what it found in the status line, which is cleared as the next look starts; the timed looks say
  // nothing, but one that found nothing built clears a failed look's line, which is no longer true. The line is mounted
  // (empty) from the start: a live region that arrives already filled is not announced
  const [said, setSaid] = useState("");
  const checkNow = (check: CheckAgain) => {
    setSaid("");
    void check().then((outcome) => setSaid(LOOK_SAYS[outcome]));
  };
  useEffect(() => { setSaid((s) => (s === LOOK_SAYS.failed ? "" : s)); }, [quietLooks]);
  return (
    <>
      <Heading title="Set up your household">
        {built
          ? <>The household is described, but no account and no hand-stated asset is listed yet, so there is nothing to
            show; until the first data arrives, {STAND_IN}</>
          : <>No ingest has built the dashboard's data yet, so there is nothing to show. Three steps set the household up
            and bring its first data; the set-up command walks you through them{" "}
            <span className="text-zinc-500">(<Code>ff init</Code>)</span>.</>}
      </Heading>
      <Card testId="card-first-run-steps" title="Three steps">
        <ol className="list-decimal space-y-3 pl-5 text-sm leading-relaxed text-zinc-300 marker:text-zinc-500">
          <Step what="Describe the household" hint={<><Code>household.yaml</Code> in the config folder</>}
            done={built ? "Done: the last ingest read this file." : undefined}>
            its people, the owner name for what they hold jointly, and its time zone.
          </Step>
          <Step what="List the accounts" hint={<><Code>accounts.yaml</Code> in the same folder; <Code>kind:</Code> is{" "}
            <Code>checking</Code>, <Code>savings</Code>, <Code>credit-card</Code>, <Code>brokerage</Code> or{" "}
            <Code>loan</Code></>}>
            each with a name, its institution, a kind and an owner — one of the household's people or the joint label.
          </Step>
          <Step what="Bring data" hint={<><Code>simplefin_id:</Code>; <Code>parser:</Code> and <Code>glob:</Code>; or{" "}
            <Code>manual_entries:</Code> in <Code>accounts.yaml</Code></>}>
            link accounts to the bank feed by their feed ids, put their statement files in the statements folder with the
            format each is in, or state an asset such as your home by hand; then {built ? <RebuildOnData /> : "re-run the ingest"}.
          </Step>
        </ol>
        {built && <EnterByHand />}
        <p className="mt-3 text-xs text-faint">
          Statement files are read only in the formats this release knows, and a brokerage's or a card's balance comes
          from the bank feed alone. {DOCS_LINE}
        </p>
        {!built && (
          <div className="mt-4 border-t border-zinc-800 pt-3 text-sm text-zinc-300">
            <p>
              Done all three? The dashboard opens here by itself once an ingest has run without refusing the config;
              when the ingest refuses it, its own output lists every problem, one per line.
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              {onCheckAgain && (
                <button type="button" onClick={() => checkNow(onCheckAgain)}
                  className="min-h-[40px] rounded-md border border-zinc-700 px-3 text-sm text-zinc-300 hover:bg-zinc-800 md:min-h-0 md:py-1">
                  Check now
                </button>
              )}
              <span className="text-xs text-faint">This page looks again {RECHECK_WORDS}.</span>
            </div>
            {onCheckAgain && <p role="status" className="mt-2 text-xs text-zinc-400">{said}</p>}
          </div>
        )}
      </Card>
    </>
  );
}

/** what brings the first data, in one sentence. A feed id says only that an account names one: whether the feed was
 *  ever fetched (`fetched`, manifest.simplefin_fetched_at) decides what to say. Statement files the last ingest found
 *  and read no row from are unreadable or empty: Data's "What the last ingest read" says what each held. With neither,
 *  where they go. */
function nextStep({ feed, import_files }: Setup, fetched: Manifest["simplefin_fetched_at"]): ReactNode {
  if (feed && fetched === null) {
    return "Nothing has been fetched from the bank feed yet; the first ingest after a fetch brings its balances and "
      + "transactions.";
  }
  if (feed) {
    return (
      <>
        The bank feed was fetched and reported nothing for these accounts: check each account's feed id against the
        feed's own ids — Data lists every account the feed reported that no account names{" "}
        <span className="text-zinc-500">(<Code>simplefin_id:</Code> in <Code>accounts.yaml</Code>)</span>.
      </>
    );
  }
  if (import_files > 0) {
    // what each file held, and why its lines were skipped, is on Data's "What the last ingest read" card
    return (
      <>
        The last ingest found them and read no rows from them (Data's{" "}
        <Link to={cardPath("/data", SCROLL_TARGETS.ingestRead)} className="text-blue-400 hover:underline">What the last
          ingest read</Link> says what each file held): check that each holds its account's statement rows in the format
        the account names{" "}
        <span className="text-zinc-500">(<Code>parser:</Code> in <Code>accounts.yaml</Code>)</span>.
      </>
    );
  }
  return (
    <>
      Link an account to the bank feed by its feed id, or put its statement files in the statements folder with the
      format it is in, then <RebuildOnData />{" "}
      <span className="text-zinc-500">(<Code>simplefin_id:</Code>, or <Code>parser:</Code> and <Code>glob:</Code>, in{" "}
        <Code>accounts.yaml</Code>)</span>.
    </>
  );
}

/** the configured household's summary, from manifest.json `setup`: its accounts, whether one names a bank-feed id (the
 *  export says whether, not how many), the statement files found, or neither (the thing to fix), and its hand-stated
 *  assets when it has any */
function summary({ accounts, feed, import_files, manual_assets }: Setup): string[] {
  const out = [accounts === 0 ? "No account configured" : `${countOf(accounts, "account")} configured`];
  if (feed) out.push("At least one account names a bank-feed id");
  if (import_files > 0) out.push(`${countOf(import_files, "statement file")} found`);
  if (!feed && import_files === 0) out.push("No bank-feed id and no statement file yet");
  if (manual_assets > 0) out.push(countOf(manual_assets, "hand-stated asset"));
  return out;
}

function Waiting() {
  const { data } = useData();
  const { setup, simplefin_fetched_at: fetched } = data.manifest;
  // when the refresh status reports a schedule and the household has a feed, when its next scheduled refresh is (on the
  // household's clock); with no schedule, or no refresh service, nothing is said of one
  const schedule = useRefreshSchedule();
  const next = setup.feed ? nextRefreshSentence(schedule, data.assumptions.household.timezone, clock.now()) : null;
  return (
    <>
      <Heading title="Waiting for data">
        The household is configured, and its first data has not arrived yet; until it does, {STAND_IN}
      </Heading>
      <Card testId="card-first-run-summary" title="What is configured">
        <ul className="list-disc space-y-1 pl-5 text-sm text-zinc-300 marker:text-zinc-500">
          {summary(setup).map((line) => <li key={line}>{line}</li>)}
        </ul>
        <p className="mt-3 text-sm text-zinc-300">{nextStep(setup, fetched)}</p>
        {next && <p data-testid="first-run-next-refresh" className="mt-2 text-sm text-zinc-300">{next}</p>}
        <EnterByHand />
        <p className="mt-3 text-xs text-faint">{DOCS_LINE}</p>
        <p className="mt-3 text-xs text-zinc-400">
          What the last ingest read and reported is on <Link to="/data" className="text-blue-400 hover:underline">Data</Link>.
        </p>
      </Card>
    </>
  );
}

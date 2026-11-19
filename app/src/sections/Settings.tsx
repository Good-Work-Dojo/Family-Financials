// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Card } from "../components/ui";
import { useData } from "../data/DataContext";
import { hasUnsentEdits } from "../data/session";
import { signOut } from "../data/signOut";
import { IS_DEMO } from "../lib/demo";
import { firstRunState } from "../lib/firstRun";
import { useScrollToCardParam } from "../lib/scrollTargets";
import AboutCard from "./settings/AboutCard";
import AiCard from "./settings/AiCard";
import ElsewhereCard from "./settings/ElsewhereCard";
import ManualAssetsCard from "./settings/ManualAssetsCard";
import ModulesCard from "./settings/ModulesCard";

/** Settings: the optional parts of the dashboard, the hand-entered assets, AI categorization, the settings that live
 *  on other pages and what the dashboard is running. Every card renders for every household, one with no data at all included: the
 *  first-run page never stands in for this page (lib/firstRun.ts FIRST_RUN_KEEPS), so while it stands in for the others
 *  one line under the heading says where its set-up steps are. */
export default function Settings() {
  const { data } = useData();
  const firstRun = firstRunState(data.manifest, data) !== null;
  // a page that needs a value entered by hand links here, to the Manual assets card (`?card=manual-assets`)
  useScrollToCardParam();
  return (
    <div className="max-w-3xl space-y-4">
      <h1 className="text-xl font-bold">Settings</h1>
      {firstRun && (
        <p data-testid="settings-first-run" className="text-sm text-zinc-300">
          Setting up? The steps are on <Link to="/" className="text-blue-400 underline hover:no-underline">Home</Link>.
        </p>
      )}
      <ModulesCard />
      <ManualAssetsCard />
      <AiCard />
      <ElsewhereCard />
      <AboutCard />
      {IS_DEMO ? <NoSignInCard /> : <SignOutCard />}
    </div>
  );
}

/** The sign-out card in the demo build, which has no sign-in: what stays where, in place of the button. */
function NoSignInCard() {
  return (
    <Card testId="card-sign-out" title="Sign out">
      <p data-testid="sign-out-demo" className="text-sm text-zinc-300">
        The demo has no sign-in. What you change here stays in this browser; clearing this site's data puts the demo back
        as it was.
      </p>
    </Card>
  );
}

const SIGN_OUT_BTN = "min-h-[40px] rounded-md border border-zinc-700 px-3 text-sm text-zinc-300 hover:bg-zinc-800 "
  + "disabled:cursor-wait disabled:opacity-60 md:min-h-0 md:py-1";

/** Sign out (data/signOut.ts): the store ends the session, this browser forgets its copy of the family's documents and
 *  the page goes to the sign-in page. With an edit the store does not have yet it asks first, the way deleting a
 *  scenario does, the focus on staying; a sign-out that does not go through says so and the page stays. */
function SignOutCard() {
  const what = useId();
  const question = useId();
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const signOutBtn = useRef<HTMLButtonElement>(null);
  const stayBtn = useRef<HTMLButtonElement>(null);
  // the question replaces the button: the focus moves to its safe answer, and back to the button when it goes
  const back = useRef(false);
  useEffect(() => {
    if (asking) stayBtn.current?.focus();
    else if (back.current) signOutBtn.current?.focus();
    back.current = false;
  }, [asking]);
  const go = async () => {
    setBusy(true);
    setFailed(false);
    if (await signOut()) return;                   // the page is leaving
    setBusy(false);
    setAsking(false);
    setFailed(true);
  };
  const press = () => {
    if (hasUnsentEdits()) setAsking(true);
    else void go();
  };
  return (
    <Card testId="card-sign-out" title="Sign out">
      <p id={what} className="mb-3 text-sm text-zinc-300">
        Ends the sign-in on this browser and clears the copy of the family's answers, settings, scenarios and assets it
        keeps.
      </p>
      {asking ? (
        <div role="group" aria-labelledby={question} className="flex flex-wrap items-center gap-2">
          <p id={question} className="w-full text-sm text-amber-300">
            You have changes that have not been saved yet. Sign out anyway?
          </p>
          <button type="button" ref={stayBtn} className={SIGN_OUT_BTN}
            onClick={() => { back.current = true; setAsking(false); }}>
            Stay signed in
          </button>
          <button type="button" disabled={busy} className={`${SIGN_OUT_BTN} text-red-300`} onClick={() => void go()}>
            Sign out anyway
          </button>
        </div>
      ) : (
        <button type="button" ref={signOutBtn} aria-describedby={what} disabled={busy} className={SIGN_OUT_BTN}
          onClick={press}>
          Sign out
        </button>
      )}
      {failed && (
        <p role="alert" className="mt-2 text-sm text-red-300">
          Sign-out did not go through, so this browser is still signed in. Try again.
        </p>
      )}
    </Card>
  );
}

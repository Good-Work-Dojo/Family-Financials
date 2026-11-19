// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useEffect, useId, useRef, useState } from "react";
import { useRefreshFollow } from "../../components/DataRefresh";
import { Code } from "../../components/NoData";
import { MissingDataCard } from "../../components/RequireData";
import { Card, LocalOnlyNotice } from "../../components/ui";
import { useData } from "../../data/DataContext";
import { useIngestFollow } from "../../data/useIngestFollow";
import type { IngestAsk, IngestMode } from "../../data/useRefresh";
import {
  type AiAction, agreed, aiAskLine, aiCardState, appliedText, canForget, consentFor, lastRunText, skippedLines, turnedOff,
} from "../../lib/aiCard";
import { clock } from "../../lib/clock";
import { IS_DEMO } from "../../lib/demo";
import { fmtDateTime } from "../../lib/format";
import { SCROLL_TARGETS } from "../../lib/scrollTargets";
import AiConsentDialog, { WhatIsSentParagraph, WhatWouldBeSent } from "./AiConsentDialog";

// Settings → AI categorization: what it is, exactly what it would send (counts from ai_status.json, which holds no
// vendor; the exact list is the preview command's), the family's consent and the switch. Four states (lib/aiCard.ts
// aiCardState): no key stored (what it is and how to set it up), off (what would be sent, and "Turn on…" opening the
// consent dialog), on (the last run, what its answers filed, Turn off, Forget AI answers) and stale (what is sent has
// changed since the family agreed: review it and turn it on again). A switch writes the family's preferences at once
// (prefs `ai`, kept whole by the store, so an older tab never erases it) and asks the store for the update it needs
// through the refresh mailbox: Turn on asks for the AI step and an ingest, Turn off for an ingest, Forget for the stored
// answers to be forgotten and an ingest; the card follows that update (lib/ingestFollow.ts) and says how it ended.

const BTN = "inline-flex min-h-[40px] items-center rounded-md border px-3 text-sm md:min-h-0 md:py-1";
const QUIET = `${BTN} border-zinc-700 text-zinc-300 hover:bg-zinc-800`;

/** what AI categorization is, said in every state */
const INTRO = "Optional. When it is on, the names of vendors your own rules have not sorted are sent to an AI service, "
  + "which suggests a category for each. Every row it files is marked AI, and your rules, answers and overrides always "
  + "come first.";

/** the demo build in place of the switches: it has no AI service to send to, and sends nothing */
export const AI_DEMO = "The demo cannot turn it on: it has no AI service, and nothing in it leaves this browser.";

/** the family agreed to an earlier version of what is sent */
const STALE = "What is sent has changed since you turned this on. Review it and turn it on again. Until then it is off, "
  + "and its answers file no row.";

export default function AiCard() {
  const { missing } = useData();
  return (
    <Card id={SCROLL_TARGETS.aiCategorization} testId="card-ai-categorization" title="AI categorization">
      {missing.has("ai_status.json")
        ? <MissingDataCard compact files={["ai_status.json"]} what="The AI categorization status" />
        : <AiBody />}
    </Card>
  );
}

function AiBody() {
  const { data, prefs } = useData();
  const status = data.aiStatus;
  const zone = data.assumptions.household.timezone;
  const ai = prefs.doc.ai;
  const state = aiCardState(status, ai);
  const refresh = useRefreshFollow();
  const { follow, dispatch, landed } = useIngestFollow(refresh, data.manifest.generated_at, "save");
  const landedRef = useRef(landed);
  landedRef.current = landed;
  const [ask, setAsk] = useState<IngestAsk | null>(null);
  const [action, setAction] = useState<AiAction>("on");
  const [dialog, setDialog] = useState(false);
  const [forgetting, setForgetting] = useState(false);
  const questionId = useId();
  // a switch replaces the buttons that were pressed: the focus goes to the line that says what happened
  const line = useRef<HTMLParagraphElement>(null);
  const focusLine = useRef(false);
  useEffect(() => {
    if (!focusLine.current) return;
    focusLine.current = false;
    line.current?.focus();
  });
  const keepBtn = useRef<HTMLButtonElement>(null);
  const forgetBtn = useRef<HTMLButtonElement>(null);
  const backToForget = useRef(false);
  useEffect(() => {
    if (forgetting) keepBtn.current?.focus();
    else if (backToForget.current) forgetBtn.current?.focus();
    backToForget.current = false;
  }, [forgetting]);

  const request = async (act: AiAction, mode: IngestMode) => {
    setAction(act);
    setAsk(null);
    focusLine.current = true;
    dispatch({ type: "save", removed: [] });
    const answer = await refresh.requestIngest(mode);
    setAsk(answer);
    if (answer.kind === "refused" || answer.kind === "unreached" || answer.kind === "signed-out") {
      dispatch({ type: "refused" });
      return;
    }
    // taken: the store touched the markers; no service: the read that follows says so
    landedRef.current(answer.kind === "accepted");
  };
  const turnOn = () => {
    prefs.setAi(consentFor(status, clock.nowISO()));
    setDialog(false);
    void request("on", "ai");
  };
  const turnOff = () => {
    prefs.setAi(turnedOff(ai));
    setForgetting(false);
    void request("off", "ingest");
  };
  const forget = () => {
    setForgetting(false);
    void request("forget", "ai_reset");
  };

  const said = aiAskLine(follow, ask, action);
  const canTurnOn = state === "off" || state === "stale";
  const openLabel = state === "off" ? "Turn on…" : state === "stale" ? "Review and turn on…"
    : state === "on" ? "See what is sent" : "See what would be sent";
  const dialogNote = state === "unconfigured" ? "It can be turned on here once a key for an AI service is stored."
    : "It is on for the whole family.";
  const busy = follow.phase === "saving";

  return (
    <div className="space-y-3">
      <p className="text-sm text-zinc-300">
        {INTRO}
        {prefs.localOnly && <LocalOnlyNotice refused={prefs.refused} />}
      </p>

      {state === "unconfigured" && (
        <>
          <p data-testid="ai-state" className="text-sm text-zinc-300">
            It is off: no key for an AI service is stored. To set it up, store a key from the AI service in the AI
            secrets folder <span className="text-zinc-500">(<Code>secrets/ai/api_key</Code>)</span> and add an{" "}
            <Code>ai:</Code> block to <Code>household.yaml</Code>. The guide “AI categorization” in the documentation
            walks through each step.
          </p>
          {ai.enabled && (agreed(status, ai)
            ? <p className="text-sm text-zinc-300">It is switched on for the family, and starts once a key is stored.</p>
            : <p data-testid="ai-stale" className="text-sm text-amber-300">{STALE}</p>)}
        </>
      )}

      {state === "off" && (
        <>
          <p data-testid="ai-state" className="text-sm text-zinc-300">It is off.</p>
          <WhatIsSentParagraph />
          <WhatWouldBeSent status={status} />
        </>
      )}

      {state === "stale" && (
        <p data-testid="ai-state" className="text-sm text-amber-300">{STALE}</p>
      )}

      {state === "on" && (
        <div data-testid="ai-state" className="space-y-1 text-sm text-zinc-300">
          <p>
            On for the whole family{ai.consented_at ? `, since ${fmtDateTime(ai.consented_at, zone)}` : ""}.
          </p>
          <p data-testid="ai-last-run">
            {lastRunText(status.last_run, zone) ?? "It has not run yet: it runs with the next refresh."}
          </p>
          <p data-testid="ai-applied">{appliedText(status.applied)}</p>
          {skippedLines(status.skipped).length > 0 && (
            <p className="text-xs text-faint">Not used: {skippedLines(status.skipped).join("; ")}.</p>
          )}
        </div>
      )}

      {IS_DEMO ? (
        <p data-testid="ai-demo" className="text-sm text-zinc-300">{AI_DEMO}</p>
      ) : forgetting ? (
        <div role="group" aria-labelledby={questionId} className="flex flex-wrap items-center gap-2">
          <p id={questionId} className="w-full text-sm text-amber-300">
            Rows filed by AI go back to Uncategorized on the next update.
            {state === "on" && " While it is on, the AI step asks about them again on a later refresh."} Forget the AI
            answers?
          </p>
          <button type="button" ref={keepBtn} className={QUIET}
            onClick={() => { backToForget.current = true; setForgetting(false); }}>
            Keep them
          </button>
          <button type="button" data-testid="ai-forget-confirm" className={`${QUIET} text-red-300`} onClick={forget}>
            Forget them
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" data-testid="ai-open-dialog" disabled={busy} onClick={() => setDialog(true)}
            className={canTurnOn ? `${BTN} border-emerald-700 bg-emerald-950 text-emerald-300 hover:bg-emerald-900`
              : QUIET}>
            {openLabel}
          </button>
          {ai.enabled && (
            <button type="button" data-testid="ai-turn-off" disabled={busy} className={QUIET} onClick={turnOff}>
              Turn off
            </button>
          )}
          {canForget(status) && (
            <button type="button" ref={forgetBtn} data-testid="ai-forget" disabled={busy} className={QUIET}
              onClick={() => setForgetting(true)}>
              Forget AI answers
            </button>
          )}
        </div>
      )}

      <p ref={line} tabIndex={-1} role="status" data-testid="ai-status-line"
        className={`text-sm outline-none ${said && !said.ok ? "text-amber-400" : "text-zinc-300"}`}>
        {said?.text ?? ""}
      </p>

      {dialog && (
        <AiConsentDialog status={status} onClose={() => setDialog(false)}
          onTurnOn={canTurnOn ? turnOn : undefined} note={canTurnOn ? undefined : dialogNote} />
      )}
    </div>
  );
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { AiRunOutcome, AiStatus } from "../data/types";
import type { IngestAsk } from "../data/useRefresh";
import { countOf, fmtDateTime } from "./format";
import { type Follow, ingestFailure } from "./ingestFollow";
import type { AiPrefs } from "./prefs";

// Settings → AI categorization, the pure part: which state the card is in, what the family's consent writes, and the
// card's sentences built from ai_status.json (counts only: no vendor ever reaches the dashboard).

/** where AI categorization stands for the card: no key stored (`unconfigured`); a key, and the family has not switched
 *  it on (`off`); switched on for what is sent now and where it goes (`on`); switched on for an earlier version of
 *  what is sent, another recipient or other accounts (`stale`: the ETL treats it as off until the family agrees
 *  again) */
export type AiCardState = "unconfigured" | "off" | "on" | "stale";

/** The card's state from the last ingest's status and the family's own consent (prefs `ai`, which a switch here changes
 *  at once, before any ingest has read it) */
export function aiCardState(status: AiStatus, ai: AiPrefs): AiCardState {
  if (!status.configured) return "unconfigured";
  if (!ai.enabled) return "off";
  return agreed(status, ai) ? "on" : "stale";
}

/** the family's consent covers what is sent now (the policy version) and its scope (who receives the vendor names,
 *  which accounts are asked about: ai_status.json `scope`), as the ETL's own check requires */
export const agreed = (status: AiStatus, ai: AiPrefs): boolean =>
  ai.consent_policy === status.policy_version && ai.consent_scope === status.scope;

/** what "Turn on" writes: switched on, for the version of what is sent and the scope the family was just shown, at
 *  `nowISO` */
export const consentFor = (status: AiStatus, nowISO: string): AiPrefs =>
  ({ enabled: true, consent_policy: status.policy_version, consent_scope: status.scope, consented_at: nowISO });

/** what "Turn off" writes: switched off, the version agreed to and when kept as they were */
export const turnedOff = (ai: AiPrefs): AiPrefs => ({ ...ai, enabled: false });

/** why vendors are kept back, in plain words (etl/ai/policy.py REASONS), each for one vendor and for several */
const WITHHELD_WORDS: Readonly<Record<keyof AiStatus["preview"]["withheld"], readonly [string, string]>> = {
  name: ["holds a name from your household or your never-send list", "hold a name from your household or your never-send list"],
  digits: ["holds a long number or a date", "hold a long number or a date"],
  address: ["looks like an e-mail or web address", "look like an e-mail or web address"],
  length: ["is too long to be a shop's name", "are too long to be a shop's name"],
  seen: ["was seen too few times to ask about", "were seen too few times to ask about"],
};

/** one line per reason any vendor is kept back for, in the policy's order ("2 vendors hold a long number or a date") */
export function withheldLines(withheld: AiStatus["preview"]["withheld"]): string[] {
  return (Object.keys(WITHHELD_WORDS) as (keyof typeof WITHHELD_WORDS)[])
    .filter((r) => withheld[r] > 0)
    .map((r) => `${countOf(withheld[r], "vendor")} ${WITHHELD_WORDS[r][withheld[r] === 1 ? 0 : 1]}`);
}

/** the provider by its name ("anthropic" → "Anthropic"); an unknown one as the export states it */
export const providerName = (provider: string): string =>
  provider === "anthropic" ? "Anthropic" : provider === "openai" ? "OpenAI" : provider;

/** who receives the vendor names: the provider by its name, or for an OpenAI-compatible service at an address of the
 *  household's own (`endpoint_host`), that service by its host ("the OpenAI-compatible service at 127.0.0.1:11434") */
export const recipientName = (status: AiStatus): string =>
  status.provider === "openai" && status.endpoint_host
    ? `the OpenAI-compatible service at ${status.endpoint_host}` : providerName(status.provider);

/** The sidebar's line on where the household's data goes: nowhere while AI categorization is off; while the last
 *  ingest found it on with a key stored, the one thing that leaves, said plainly ("local-only · vendor names go to
 *  Anthropic for sorting"); switched on with no key, that they will once one is stored. */
export const localOnlyLine = (status: AiStatus): string =>
  !status.enabled ? "local-only · no outside request"
    : status.configured ? `local-only · vendor names go to ${recipientName(status)} for sorting`
      : `local-only · vendor names will go to ${recipientName(status)} for sorting once a key is stored`;

/** "Forget AI answers" is offered whenever there is something to forget: answers are stored, or rows are filed by
 *  them, whatever the switch */
export const canForget = (status: AiStatus): boolean => status.answers_stored || status.applied.rows > 0;

/** how the newest run ended, in words, when it did not store answers */
const OUTCOME_WORDS: Readonly<Record<Exclude<AiRunOutcome, "answered">, string>> = {
  asked: "it was still waiting for an answer",
  not_stored: "it was answered, but the answers could not be saved",
  rejected_key: "the AI service refused the key",
  rate_limited: "the AI service asked it to wait",
  unavailable: "the AI service was unavailable",
  timed_out: "it took too long and was stopped",
  deadline: "it took too long and was stopped",
  refused: "the AI service declined to answer",
  malformed: "the answer was not in the form asked for",
  request_refused: "the AI service refused the request",
  redirect: "the AI service's address sent it elsewhere, so it stopped",
  failed: "it failed",
};

/** the newest run in one sentence, its time in the household's zone; null before any run */
export function lastRunText(run: AiStatus["last_run"], zone: string | null): string | null {
  if (run === null) return null;
  const when = `Last run ${fmtDateTime(run.at, zone)}`;
  if (run.outcome === "answered") {
    return `${when}: asked about ${countOf(run.sent, "vendor")}, ${run.answered.toLocaleString("en-US")} answered, `
      + `${run.cached.toLocaleString("en-US")} saved.`;
  }
  return `${when}: ${OUTCOME_WORDS[run.outcome]}. Nothing new was saved.`;
}

/** what the stored answers filed, in one sentence */
export const appliedText = (applied: AiStatus["applied"]): string => applied.rows === 0
  ? "No transaction is filed by AI yet."
  : `Filed by AI: ${countOf(applied.rows, "transaction")} from ${countOf(applied.vendors, "vendor")}.`;

/** the stored answers the last ingest did not use, by reason, in plain words (none: an empty list) */
export function skippedLines(skipped: AiStatus["skipped"]): string[] {
  const out: string[] = [];
  if (skipped.low_confidence > 0) out.push(`${countOf(skipped.low_confidence, "answer")} not sure enough to use`);
  if (skipped.stale_category > 0) {
    out.push(`${countOf(skipped.stale_category, "answer")} naming a category you no longer have`);
  }
  if (skipped.not_candidate > 0) {
    out.push(`${countOf(skipped.not_candidate, "answer")} for a shop your rules or answers now cover`);
  }
  return out;
}

/** what the card asked the store for: AI switched on (the AI step, then an ingest), off (an ingest without its answers)
 *  or its answers forgotten (then an ingest) */
export type AiAction = "on" | "off" | "forget";

/** The line under the card's buttons for the request it made, by where the follow stands (lib/ingestFollow.ts); null
 *  says nothing. A switch is saved in the family's preferences whatever the store answers the request with: only the
 *  update it asks for can be refused. */
export function aiAskLine(f: Follow, ask: IngestAsk | null, action: AiAction): { text: string; ok: boolean } | null {
  const saved = action === "forget" ? "Asked." : "Saved.";
  switch (f.phase) {
    case "idle": return null;
    case "saving": return { text: "Asking for an update…", ok: true };
    case "asking": case "waiting": case "reloading":
      return { text: action === "on" ? "Saved. The AI step runs, then the dashboard updates."
        : action === "off" ? "Saved. The dashboard updates without the AI answers."
        : "Asked. The AI answers are forgotten, then the dashboard updates.", ok: true };
    case "done":
      return { text: action === "on" ? "Updated. The last run above says what the AI step did."
        : action === "off" ? "Updated. AI answers no longer file any row."
        : "Updated. Rows filed by AI are back to Uncategorized.", ok: true };
    case "failed":
      return { text: `${saved} The update ${ingestFailure(f.rc ?? 0)}, so the dashboard still shows what it showed before.`,
               ok: false };
    case "later": return { text: `${saved} The dashboard shows it after the next scheduled refresh.`, ok: true };
    case "no-service":
      return { text: `${saved} There is no refresh service here, so it shows after the next update run by hand.`, ok: true };
    case "refused": {
      if (ask?.kind === "signed-out") {
        return { text: `${action === "forget" ? "Not asked" : "Saved in this browser"}: the sign-in has ended. Sign in `
          + "again, then try again.", ok: false };
      }
      if (ask?.kind === "unreached") {
        return { text: "The request did not reach the dashboard's store: try again in a moment.", ok: false };
      }
      const reason = ask?.kind === "refused" ? ask.reason : null;
      if (reason === "ingest_cap") {
        return { text: action === "forget" ? "Today's updates are used up: try again after midnight."
          : "Saved. Today's updates are used up, so it shows after the next scheduled refresh.", ok: action !== "forget" };
      }
      return { text: `${action === "forget" ? "" : "Saved. "}The update was refused${reason ? `: ${reason}` : ""}.`,
               ok: false };
    }
  }
}

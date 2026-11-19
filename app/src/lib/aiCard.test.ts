// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { AI_STATUS_NONE, type AiStatus } from "../data/types";
import {
  aiAskLine, aiCardState, appliedText, consentFor, lastRunText, localOnlyLine, providerName, skippedLines, turnedOff,
  withheldLines,
} from "./aiCard";
import { type FollowPhase, IDLE_FOLLOW } from "./ingestFollow";
import { AI_OFF } from "./prefs";

const STATUS: AiStatus = { ...AI_STATUS_NONE, configured: true, provider: "anthropic", model: "claude-opus-5-5",
  policy_version: 3, scope: "anthropic||cards" };
const ON = { enabled: true, consent_policy: 3, consent_scope: "anthropic||cards", consented_at: "2026-06-01T14:00:00.000Z" };

describe("aiCardState", () => {
  it("no key stored is unconfigured, whatever the family switched", () => {
    expect(aiCardState({ ...STATUS, configured: false }, AI_OFF)).toBe("unconfigured");
    expect(aiCardState({ ...STATUS, configured: false }, ON)).toBe("unconfigured");
    expect(aiCardState(AI_STATUS_NONE, ON), "an export without the status file").toBe("unconfigured");
  });
  it("with a key: off until switched on; on for the current version of what is sent; stale for another", () => {
    expect(aiCardState(STATUS, AI_OFF)).toBe("off");
    expect(aiCardState(STATUS, { ...ON, enabled: false })).toBe("off");
    expect(aiCardState(STATUS, ON)).toBe("on");
    expect(aiCardState({ ...STATUS, policy_version: 4 }, ON), "what is sent has changed since").toBe("stale");
    expect(aiCardState(STATUS, { ...ON, consent_policy: null }), "switched on for no version").toBe("stale");
  });
  it("consent given to another recipient or other accounts is stale too, as the ETL reads it", () => {
    for (const scope of ["openai|127.0.0.1:11434|cards", "anthropic||all", null]) {
      expect(aiCardState({ ...STATUS, enabled: false }, { ...ON, consent_scope: scope }), String(scope)).toBe("stale");
    }
    expect(aiCardState({ ...STATUS, scope: "anthropic||all" }, ON), "the household widened the accounts").toBe("stale");
  });
});

describe("where the sidebar says the data goes", () => {
  it("nowhere while off; to the recipient while on with a key; once a key is stored while on without one", () => {
    expect(localOnlyLine(STATUS)).toBe("local-only · no outside request");
    expect(localOnlyLine({ ...STATUS, enabled: true })).toBe("local-only · vendor names go to Anthropic for sorting");
    expect(localOnlyLine({ ...STATUS, enabled: true, configured: false }))
      .toBe("local-only · vendor names will go to Anthropic for sorting once a key is stored");
    expect(localOnlyLine({ ...STATUS, enabled: true, provider: "openai", endpoint_host: "127.0.0.1:11434" }))
      .toBe("local-only · vendor names go to the OpenAI-compatible service at 127.0.0.1:11434 for sorting");
  });
});

describe("what the switch writes", () => {
  it("Turn on records the version shown and the instant; Turn off keeps them and switches off", () => {
    const at = "2026-06-10T13:30:00.000Z";
    expect(consentFor(STATUS, at)).toEqual({ enabled: true, consent_policy: 3, consent_scope: "anthropic||cards",
      consented_at: at });
    expect(turnedOff(ON)).toEqual({ enabled: false, consent_policy: 3, consent_scope: "anthropic||cards",
      consented_at: ON.consented_at });
    expect(ON.enabled, "the consent passed in is not changed").toBe(true);
  });
});

describe("the card's sentences", () => {
  it("says why vendors are kept back, one line per reason that keeps any, in the policy's order, one or several", () => {
    expect(withheldLines({ name: 1, digits: 0, address: 2, length: 0, seen: 5 })).toEqual([
      "1 vendor holds a name from your household or your never-send list",
      "2 vendors look like an e-mail or web address",
      "5 vendors were seen too few times to ask about",
    ]);
    expect(withheldLines({ name: 0, digits: 3, address: 0, length: 1, seen: 0 })).toEqual([
      "3 vendors hold a long number or a date", "1 vendor is too long to be a shop's name"]);
    expect(withheldLines(AI_STATUS_NONE.preview.withheld)).toEqual([]);
  });
  it("names the providers, and an unknown one as the export states it", () => {
    expect(providerName("anthropic")).toBe("Anthropic");
    expect(providerName("openai")).toBe("OpenAI");
    expect(providerName("other")).toBe("other");
  });
  it("says how the last run went, its time in the household's zone, counts only", () => {
    const run = { at: "2026-06-10T13:30:00Z", outcome: "answered" as const, sent: 12, answered: 11, cached: 10 };
    expect(lastRunText(null, "UTC")).toBeNull();
    expect(lastRunText(run, "UTC")).toMatch(/^Last run .*1:30.*: asked about 12 vendors, 11 answered, 10 saved\.$/);
    expect(lastRunText(run, "America/New_York")).toMatch(/9:30/);
    expect(lastRunText({ ...run, outcome: "rejected_key" }, "UTC"))
      .toMatch(/: the AI service refused the key\. Nothing new was saved\.$/);
    expect(lastRunText({ ...run, outcome: "timed_out" }, "UTC")).toMatch(/took too long and was stopped/);
  });
  it("counts what the answers filed and what they did not", () => {
    expect(appliedText({ vendors: 0, rows: 0 })).toBe("No transaction is filed by AI yet.");
    expect(appliedText({ vendors: 1, rows: 4 })).toBe("Filed by AI: 4 transactions from 1 vendor.");
    expect(skippedLines({ low_confidence: 0, stale_category: 0, not_candidate: 0 })).toEqual([]);
    expect(skippedLines({ low_confidence: 2, stale_category: 1, not_candidate: 3 })).toEqual([
      "2 answers not sure enough to use", "1 answer naming a category you no longer have",
      "3 answers for a shop your rules or answers now cover"]);
  });
});

describe("aiAskLine: the line under the buttons for the request the card made", () => {
  const at = (phase: FollowPhase, over: object = {}) => ({ ...IDLE_FOLLOW, phase, ...over });
  it("says nothing before a request, and what is under way while it is", () => {
    expect(aiAskLine(IDLE_FOLLOW, null, "on")).toBeNull();
    expect(aiAskLine(at("saving"), null, "on")?.text).toBe("Asking for an update…");
    expect(aiAskLine(at("waiting"), { kind: "accepted" }, "on")?.text).toBe("Saved. The AI step runs, then the dashboard updates.");
    expect(aiAskLine(at("waiting"), { kind: "accepted" }, "off")?.text).toBe("Saved. The dashboard updates without the AI answers.");
    expect(aiAskLine(at("asking"), { kind: "accepted" }, "forget")?.text)
      .toBe("Asked. The AI answers are forgotten, then the dashboard updates.");
  });
  it("says how it ended", () => {
    expect(aiAskLine(at("done"), { kind: "accepted" }, "forget")).toEqual(
      { text: "Updated. Rows filed by AI are back to Uncategorized.", ok: true });
    expect(aiAskLine(at("failed", { rc: -1 }), { kind: "accepted" }, "on")).toEqual(
      { text: "Saved. The update timed out, so the dashboard still shows what it showed before.", ok: false });
    expect(aiAskLine(at("no-service"), { kind: "no-service" }, "off")?.text).toMatch(/^Saved\. There is no refresh service/);
  });
  it("a refused request: the switch is saved all the same, a forget is not asked", () => {
    const cap = { kind: "refused" as const, reason: "ingest_cap" };
    expect(aiAskLine(at("refused"), cap, "on")?.text).toBe(
      "Saved. Today's updates are used up, so it shows after the next scheduled refresh.");
    expect(aiAskLine(at("refused"), cap, "forget")).toEqual(
      { text: "Today's updates are used up: try again after midnight.", ok: false });
    expect(aiAskLine(at("refused"), { kind: "signed-out" }, "forget")?.text).toMatch(/^Not asked: the sign-in has ended/);
    expect(aiAskLine(at("refused"), { kind: "unreached" }, "off")?.text).toMatch(/did not reach the dashboard's store/);
    expect(aiAskLine(at("refused"), { kind: "refused", reason: "cross-origin refresh refused" }, "forget")?.text)
      .toBe("The update was refused: cross-origin refresh refused.");
  });
});

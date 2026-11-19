// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AI_STATUS_NONE, type AiStatus, type AppData, type ManualAsset } from "../data/types";
import { appDataFactory, mortgageFactory, snapFactory } from "../lib/__fixtures__";
import { clock } from "../lib/clock";
import { fmtDateTime } from "../lib/format";
import { OpenRoutesProvider } from "../lib/modules";
import { type AiPrefs, emptyPrefs, type PrefsDoc } from "../lib/prefs";

const base = appDataFactory(snapFactory())({ manifest: { ...appDataFactory(snapFactory())().manifest,
  generated_at: "2026-06-10T09:30:00-04:00" } });
let data: AppData = base;
let missing = new Set<string>();
const prefs = { doc: emptyPrefs() as PrefsDoc, localOnly: false, refused: null as number | null, setModule: vi.fn(),
  setAi: vi.fn() };
// what the card asked the refresh mailbox for (each POST's body), and how the store answers a POST
let posts: { mode?: string }[] = [];
let refreshAnswer = { status: 202, body: { accepted: true } as Record<string, unknown> };
vi.mock("../data/DataContext", () => ({
  useData: () => ({ data, missing, prefs, indexes: { dataRange: null }, reload: async () => {} }),
}));

import { RefreshProvider } from "../components/DataRefresh";
import { leave, reportUnsent, resetSession } from "../data/session";
import { FAMILY_MIRRORS } from "../data/signOut";
import Settings from "./Settings";
import AboutCard, { COPYRIGHT, LICENCE_LINE } from "./settings/AboutCard";
import { DEFAULT_SOURCE_URL } from "../lib/product";

beforeEach(() => {
  data = base;
  missing = new Set();
  prefs.doc = emptyPrefs();
  prefs.localOnly = false;
  prefs.refused = null;
  prefs.setModule = vi.fn();
  prefs.setAi = vi.fn();
  posts = [];
  refreshAnswer = { status: 202, body: { accepted: true } };
  // the family store holds the hand-entered assets as the exports list them (its own tests: ManualAssetsCard.test.tsx);
  // a POST to the refresh mailbox is noted and answered as refreshAnswer says
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (String(url).endsWith("api/manual-assets")) {
      return new Response(JSON.stringify({ version: 1, updated_at: "1970-01-01T00:00:00Z", assets: [] }), { status: 200 });
    }
    if (String(url).endsWith("api/refresh") && init?.method === "POST") {
      posts.push(JSON.parse(String(init.body)));
      return new Response(JSON.stringify(refreshAnswer.body), { status: refreshAnswer.status });
    }
    return new Response("{}", { status: 404 });
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const mount = (path = "/settings") =>
  render(<MemoryRouter initialEntries={[path]}><RefreshProvider><Settings /></RefreshProvider></MemoryRouter>);
const card = (id: string) => screen.getByTestId(id);
const withModules = (modules: Record<string, boolean>) => ({ ...base, assumptions: { ...base.assumptions, modules } });
const asset = (pin: Partial<ManualAsset>): ManualAsset => ({ id: "home", name: "Home", institution: null, owner: "Joint",
  asset_class: "House", liquid: false, acquired: null, values: [{ as_of: "2026-01-01", value: 500000 }], origin: "config",
  ...pin });
/** a household in the "waiting" first-run state: accounts listed, no data arrived */
const waitingData = (): AppData =>
  ({ ...base, transactions: [], purchases: [], assets: { snapshots: [] }, balances: { series: {}, accounts: {} } });
/** a household in the "setup" first-run state: nothing listed at all */
const setupData = (): AppData => {
  const w = waitingData();
  return { ...w, accounts: [], manifest: { ...w.manifest, setup: { accounts: 0, feed: false, import_files: 0, manual_assets: 0 } } };
};

describe("Settings: the page", () => {
  it("in a first-run state, one line at the top says where the set-up steps are, with a link to Home", () => {
    for (const state of [waitingData(), setupData()]) {
      cleanup();
      data = state;
      mount();
      const line = screen.getByTestId("settings-first-run");
      expect(line.textContent).toBe("Setting up? The steps are on Home.");
      expect(within(line).getByRole("link", { name: "Home" }).getAttribute("href")).toBe("/");
      expect(line.compareDocumentPosition(card("card-modules")) & Node.DOCUMENT_POSITION_FOLLOWING, "above the cards")
        .toBeTruthy();
    }
    cleanup();
    data = base;
    mount();
    expect(screen.queryByTestId("settings-first-run"), "absent once there is data").toBeNull();
  });
  it("has one h1 and its six cards in order, and promises nothing", () => {
    mount();
    expect(screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent)).toEqual(["Settings"]);
    const ids = [...document.querySelectorAll("[data-testid^='card-']")].map((el) => el.getAttribute("data-testid"));
    expect(ids).toEqual(["card-modules", "card-manual-assets", "card-ai-categorization", "card-elsewhere", "card-about",
      "card-sign-out"]);
    expect(document.body.textContent).not.toMatch(/not built yet|Coming to this page|coming soon/i);
  });
});

describe("Settings → Modules", () => {
  const theSwitch = () => within(card("card-modules")).getByRole("switch", { name: "Home buying" });
  const source = () => within(card("card-modules")).getByTestId("module-source").textContent;
  const backButton = () => within(card("card-modules")).queryByRole("button", { name: /for Home buying$/ });

  it("one labelled switch per module, described by what it adds and where its value comes from: off by default", () => {
    mount();
    const sw = theSwitch();
    expect(sw.getAttribute("aria-checked")).toBe("false");
    const described = (sw.getAttribute("aria-describedby") ?? "").split(" ").map((id) => document.getElementById(id)?.textContent);
    expect(described).toEqual(["Adds a Second house page under Planning that works through buying another home: its "
      + "costs, how you would fund it and the cash it would leave.", "Off (the default)"]);
    expect(within(card("card-modules")).getAllByRole("switch")).toHaveLength(1);
    expect(backButton(), "nothing to go back to").toBeNull();
  });
  it("a click switches it for the family: setModule with the other value", () => {
    mount();
    fireEvent.click(theSwitch());
    expect(prefs.setModule).toHaveBeenCalledWith("home_buying", true);
    cleanup();
    data = withModules({ home_buying: true });
    mount();
    fireEvent.click(theSwitch());
    expect(prefs.setModule).toHaveBeenLastCalledWith("home_buying", false);
  });
  it("says when the value comes from the household's config, with the key as a hint", () => {
    data = withModules({ home_buying: true });
    mount();
    expect(theSwitch().getAttribute("aria-checked")).toBe("true");
    expect(source()).toBe("On in the household's config (modules: in household.yaml)");
    expect([...card("card-modules").querySelectorAll("[data-testid='module-source'] code")].map((c) => c.textContent))
      .toEqual(["modules:", "household.yaml"]);
    expect(backButton()).toBeNull();
    cleanup();
    data = withModules({ home_buying: false });
    mount();
    expect(source()).toBe("Off in the household's config (modules: in household.yaml)");
  });
  it("a switch made in the dashboard says so and offers the config's setting back, which clears it and keeps focus", () => {
    data = withModules({ home_buying: true });
    prefs.doc = { ...emptyPrefs(), modules: { home_buying: false } };
    mount();
    expect(theSwitch().getAttribute("aria-checked"), "the family's switch wins over the config").toBe("false");
    expect(source()).toBe("Off for the whole family, switched in the dashboard");
    const back = backButton()!;
    expect(back.textContent, "its visible words say what it gives").toBe("Use the config's setting (on)");
    expect(back.getAttribute("aria-label"), "and begin its name").toBe("Use the config's setting (on) for Home buying");
    fireEvent.click(back);
    expect(prefs.setModule).toHaveBeenCalledWith("home_buying", null);
    expect(document.activeElement, "focus goes to the switch, not the document").toBe(theSwitch());
  });
  it("the way back says what it gives: the config's on or off, or the default when the config states nothing", () => {
    const cases: [Record<string, boolean>, boolean, string][] = [
      [{ home_buying: true }, false, "Use the config's setting (on)"],
      [{ home_buying: false }, true, "Use the config's setting (off)"],
      [{}, true, "Go back to the default (off)"],
    ];
    for (const [seed, mine, words] of cases) {
      cleanup();
      data = withModules(seed);
      prefs.doc = { ...emptyPrefs(), modules: { home_buying: mine } };
      mount();
      expect(backButton()?.textContent, JSON.stringify(seed)).toBe(words);
    }
  });
  it("while the store is unreachable the switch is this browser's, and the card says so", () => {
    prefs.doc = { ...emptyPrefs(), modules: { home_buying: true } };
    prefs.localOnly = true;
    mount();
    expect(source()).toBe("On in this browser");
    expect(card("card-modules").textContent).toContain("saved in this browser only for now");
  });
  it("says what brings the House page only while the household has no mortgage account", () => {
    mount();
    expect(screen.getByTestId("house-needs-mortgage").textContent)
      .toBe("The House page appears when an account is marked as the mortgage (mortgage: true in accounts.yaml).");
    cleanup();
    data = { ...base, assumptions: { ...base.assumptions, mortgage: mortgageFactory() } };
    mount();
    expect(screen.queryByTestId("house-needs-mortgage")).toBeNull();
  });
});

describe("Settings → Manual assets", () => {
  it("says what a hand-entered asset is, and with none what brings one: an asset added here or the config", async () => {
    mount();
    const c = card("card-manual-assets");
    expect(within(c).getByRole("heading", { name: "Manual assets" })).toBeDefined();
    expect(c.textContent).toContain("A hand-entered asset is something the household owns that no account reports — a home, a vehicle");
    expect((await within(c).findByTestId("empty-state")).textContent)
      .toBe("None yet. Add one here, or state one in the household's config (manual_entries: in accounts.yaml).");
    expect(within(c).getByRole("button", { name: "Add an asset" })).toBeDefined();
  });
  it("lists the household's assets with where each was entered", async () => {
    data = { ...base, manualAssets: [asset({}), asset({ id: "car", name: "Family car", asset_class: "Car", origin: "app" })] };
    mount();
    const c = card("card-manual-assets");
    expect((await within(c).findByTestId("manual-assets-count")).textContent).toBe("2 hand-entered assets");
    expect(within(c).getByTestId("manual-asset-home").textContent).toContain("from your config");
    expect(within(c).getByTestId("manual-asset-car").textContent, "the store holds no entry for it")
      .toContain("the family store no longer holds it");
  });
  it("a link to /settings?card=manual-assets lands on the card, focus on its heading", () => {
    const scrolled: string[] = [];
    const before = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element) { scrolled.push(this.id); };
    try {
      mount("/settings?card=manual-assets");
      expect(scrolled).toEqual(["manual-assets"]);
      expect(document.activeElement).toBe(card("card-manual-assets").querySelector("h2"));
    } finally {
      Element.prototype.scrollIntoView = before;
    }
  });
  it("with accounts.json missing it says the list did not load, never that there is none", () => {
    missing = new Set(["accounts.json"]);
    mount();
    const c = card("card-manual-assets");
    expect(within(c).getByTestId("missing-data")).toBeDefined();
    expect(c.textContent).not.toContain("None yet");
  });
});

describe("Settings → AI categorization", () => {
  const STATUS: AiStatus = { ...AI_STATUS_NONE, configured: true, provider: "anthropic", model: "claude-opus-5-5",
    policy_version: 2, scope: "anthropic||cards", caps: { vendors_per_run: 50, runs_per_day: 4 },
    preview: { vendors: 7, withheld: { name: 2, digits: 1, address: 0, length: 0, seen: 3 } } };
  const ON = { enabled: true, consent_policy: 2, consent_scope: "anthropic||cards",
    consented_at: "2026-06-01T14:00:00.000Z" };
  const withStatus = (s: Partial<AiStatus>) => { data = { ...base, aiStatus: { ...STATUS, ...s } }; };
  const ai = () => card("card-ai-categorization");
  const button = (name: RegExp | string) => within(ai()).getByRole("button", { name });
  const dialog = () => screen.getByRole("dialog", { name: "What AI categorization sends" });
  const codes = (el: HTMLElement) => [...el.querySelectorAll("code")].map((c) => c.textContent);
  const asked = () => posts.map((p) => p.mode);

  it("with no key stored: what it is, that it is off, how to set it up in words with the config as hints", () => {
    data = { ...base, aiStatus: AI_STATUS_NONE };
    mount();
    expect(within(ai()).getByTestId("ai-state").textContent).toBe("It is off: no key for an AI service is stored. To "
      + "set it up, store a key from the AI service in the AI secrets folder (secrets/ai/api_key) and add an ai: block to "
      + "household.yaml. The guide “AI categorization” in the documentation walks through each step.");
    expect(codes(within(ai()).getByTestId("ai-state"))).toEqual(["secrets/ai/api_key", "ai:", "household.yaml"]);
    expect(within(ai()).queryByRole("button", { name: "Turn off" })).toBeNull();
    expect(within(ai()).queryByRole("button", { name: "Forget AI answers" })).toBeNull();
    // what would be sent can be seen, but there is nothing to turn on yet
    fireEvent.click(button("See what would be sent"));
    expect(within(dialog()).queryByRole("button", { name: "Turn on" })).toBeNull();
    expect(dialog().textContent).toContain("It can be turned on here once a key for an AI service is stored.");
    fireEvent.click(within(dialog()).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("with no key but switched on (a key since removed), it says so and can be turned off", () => {
    data = { ...base, aiStatus: { ...AI_STATUS_NONE, policy_version: 2, scope: "anthropic||cards" } };
    prefs.doc = { ...emptyPrefs(), ai: ON };
    mount();
    expect(ai().textContent).toContain("It is switched on for the family, and starts once a key is stored.");
    fireEvent.click(button("Turn off"));
    expect(prefs.setAi).toHaveBeenCalledWith({ ...ON, enabled: false });
  });
  it("with no key but switched on for an earlier version of what is sent, it says what is sent has changed", () => {
    data = { ...base, aiStatus: { ...AI_STATUS_NONE, policy_version: 3 } };
    prefs.doc = { ...emptyPrefs(), ai: ON };
    mount();
    expect(ai().textContent).not.toContain("starts once a key is stored");
    expect(within(ai()).getByTestId("ai-stale").textContent).toBe("What is sent has changed since you turned this on. "
      + "Review it and turn it on again. Until then it is off, and its answers file no row.");
  });
  it("names an OpenAI-compatible service at the household's own address by its host", () => {
    withStatus({ provider: "openai", model: "local-model", endpoint_host: "127.0.0.1:11434" });
    mount();
    expect(within(ai()).getByTestId("ai-preview").textContent).toContain(
      "Sent to the OpenAI-compatible service at 127.0.0.1:11434 (local-model); this is version 2 of what is sent.");
    cleanup();
    withStatus({ provider: "openai", model: "gpt-sample", endpoint_host: null });
    mount();
    expect(within(ai()).getByTestId("ai-preview").textContent).toContain("Sent to OpenAI (gpt-sample);");
  });
  it("off: the paragraph, the counts, the provider and version, and how to see the exact list", () => {
    withStatus({});
    mount();
    expect(within(ai()).getByTestId("ai-state").textContent).toBe("It is off.");
    const sent = within(ai()).getByTestId("ai-what-is-sent");
    expect(sent.textContent).toMatch(/^What is sent: the names of vendors your rules have not sorted.* Never an amount, a date, an account or a note/);
    // what a vendor name can carry, the one action, and the command last, in the pill
    expect(sent.textContent).toContain("A vendor name can include the shop's town and, for a person paid through a "
      + "card reader (a tutor who takes cards), that person's name: add such names to the never-send list, and look at "
      + "the list on the computer that runs the dashboard before turning it on: ff ai preview");
    expect(sent.textContent?.endsWith("before turning it on: ff ai preview")).toBe(true);
    expect(codes(sent)).toEqual(["ai.never_send", "household.yaml", "ff ai preview"]);
    const preview = within(ai()).getByTestId("ai-preview");
    expect(preview.textContent).toContain("7 vendor names would be sent now. Kept back, and never sent:");
    expect([...preview.querySelectorAll("li")].map((li) => li.textContent)).toEqual([
      "2 vendors hold a name from your household or your never-send list", "1 vendor holds a long number or a date",
      "3 vendors were seen too few times to ask about"]);
    expect(preview.textContent).toContain("Sent to Anthropic (claude-opus-5-5); this is version 2 of what is sent. Each run "
      + "asks about at most 50 vendors, at most 4 runs a day.");
    expect(codes(preview)).toEqual(["ff ai preview"]);
    expect(within(ai()).queryByRole("button", { name: "Turn off" })).toBeNull();
  });
  it("Turn on… opens the consent dialog, which repeats what is sent; Not now changes nothing", () => {
    withStatus({});
    mount();
    fireEvent.click(button("Turn on…"));
    const d = dialog();
    expect(d.getAttribute("aria-modal")).toBe("true");
    expect(document.activeElement, "the dialog takes the focus, not its Turn on button").toBe(d);
    // the card's own copy comes first; the dialog's repeats it word for word
    for (const id of ["ai-what-is-sent", "ai-preview"]) {
      const [onCard, inDialog] = within(ai()).getAllByTestId(id);
      expect(d.contains(onCard), `${id}: the card's own`).toBe(false);
      expect(inDialog.textContent, id).toBe(onCard.textContent);
      expect(within(d).getByTestId(id)).toBe(inDialog);
    }
    fireEvent.click(within(d).getByRole("button", { name: "Not now" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(prefs.setAi).not.toHaveBeenCalled();
    expect(posts).toEqual([]);
  });
  it("Turn on records the family's consent to the version shown, and asks for the AI step", async () => {
    withStatus({});
    const instant = vi.spyOn(clock, "nowISO").mockReturnValue("2026-06-10T14:05:00.000Z");
    try {
      mount();
      fireEvent.click(button("Turn on…"));
      fireEvent.click(within(dialog()).getByRole("button", { name: "Turn on" }));
    } finally {
      instant.mockRestore();
    }
    expect(prefs.setAi).toHaveBeenCalledTimes(1);
    expect(prefs.setAi.mock.calls[0][0] as AiPrefs, "stamped with the clock's instant")
      .toEqual({ enabled: true, consent_policy: 2, consent_scope: "anthropic||cards",
        consented_at: "2026-06-10T14:05:00.000Z" });
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(asked()).toEqual(["ai"]));
    await waitFor(() => expect(within(ai()).getByTestId("ai-status-line").textContent)
      .toBe("Saved. The AI step runs, then the dashboard updates."));
  });
  it("on: when it was agreed to, the last run and what its answers filed, counts only", () => {
    withStatus({ enabled: true, consent_policy: 2, applied: { vendors: 3, rows: 9 },
      last_run: { at: "2026-06-09T13:30:00Z", outcome: "answered", sent: 7, answered: 6, cached: 6 },
      skipped: { low_confidence: 1, stale_category: 0, not_candidate: 0 } });
    prefs.doc = { ...emptyPrefs(), ai: ON };
    mount();
    expect(within(ai()).getByTestId("ai-state").textContent).toMatch(/^On for the whole family, since Jun 1.*10:00/);
    expect(within(ai()).getByTestId("ai-last-run").textContent).toMatch(/^Last run Jun 9.*9:30.*: asked about 7 vendors, 6 answered, 6 saved\.$/);
    expect(within(ai()).getByTestId("ai-applied").textContent).toBe("Filed by AI: 9 transactions from 3 vendors.");
    expect(ai().textContent).toContain("Not used: 1 answer not sure enough to use.");
    expect(within(ai()).queryByTestId("ai-preview"), "the card is not the consent screen while it is on").toBeNull();
    fireEvent.click(button("See what is sent"));
    expect(within(dialog()).queryByRole("button", { name: "Turn on" })).toBeNull();
    expect(dialog().textContent).toContain("It is on for the whole family.");
  });
  it("Turn off switches it off at once, keeping what was agreed to, and asks for an update without its answers", async () => {
    withStatus({ enabled: true, consent_policy: 2 });
    prefs.doc = { ...emptyPrefs(), ai: ON };
    mount();
    fireEvent.click(button("Turn off"));
    expect(prefs.setAi).toHaveBeenCalledWith({ ...ON, enabled: false });
    await waitFor(() => expect(asked()).toEqual(["ingest"]));
  });
  it("Forget AI answers is offered whenever answers are stored or rows are filed by them, in every state", () => {
    const cases: [string, Partial<AiStatus>, AiPrefs | null, boolean][] = [
      ["on, nothing stored yet", { enabled: true, consent_policy: 2 }, ON, false],
      ["on, answers stored", { enabled: true, consent_policy: 2, answers_stored: true }, ON, true],
      ["on, rows filed", { enabled: true, consent_policy: 2, applied: { vendors: 1, rows: 2 } }, ON, true],
      ["off, answers stored", { answers_stored: true }, null, true],
      ["off, nothing stored", {}, null, false],
      ["stale, answers stored", { policy_version: 3, consent_policy: 2, answers_stored: true }, ON, true],
      ["no key, answers stored", { configured: false, answers_stored: true }, null, true],
    ];
    for (const [name, status, consent, offered] of cases) {
      cleanup();
      withStatus(status);
      prefs.doc = consent ? { ...emptyPrefs(), ai: consent } : emptyPrefs();
      mount();
      expect(within(ai()).queryByRole("button", { name: "Forget AI answers" }) !== null, name).toBe(offered);
    }
  });
  it("Forget AI answers asks first; Keep them asks nothing, Forget them asks the store to forget them", async () => {
    withStatus({ enabled: true, consent_policy: 2, answers_stored: true });
    prefs.doc = { ...emptyPrefs(), ai: ON };
    mount();
    fireEvent.click(button("Forget AI answers"));
    const question = within(ai()).getByRole("group");
    expect(question.textContent).toContain("Rows filed by AI go back to Uncategorized on the next update.");
    expect(document.activeElement?.textContent, "the focus on the safe answer").toBe("Keep them");
    fireEvent.click(within(question).getByRole("button", { name: "Keep them" }));
    expect(document.activeElement?.textContent).toBe("Forget AI answers");
    expect(posts).toEqual([]);
    fireEvent.click(button("Forget AI answers"));
    fireEvent.click(within(ai()).getByRole("button", { name: "Forget them" }));
    await waitFor(() => expect(asked()).toEqual(["ai_reset"]));
    expect(prefs.setAi, "forgetting the answers leaves the switch as it is").not.toHaveBeenCalled();
    await waitFor(() => expect(within(ai()).getByTestId("ai-status-line").textContent)
      .toBe("Asked. The AI answers are forgotten, then the dashboard updates."));
  });
  it("a request the store refuses says why; the switch was saved all the same", async () => {
    withStatus({});
    refreshAnswer = { status: 429, body: { accepted: false, reason: "ingest_cap" } };
    mount();
    fireEvent.click(button("Turn on…"));
    fireEvent.click(within(dialog()).getByRole("button", { name: "Turn on" }));
    await waitFor(() => expect(within(ai()).getByTestId("ai-status-line").textContent)
      .toBe("Saved. Today's updates are used up, so it shows after the next scheduled refresh."));
    expect(prefs.setAi).toHaveBeenCalledTimes(1);
  });
  it("stale: what is sent has changed since it was agreed to: review it and turn it on again", () => {
    withStatus({ policy_version: 3, consent_policy: 2 });
    prefs.doc = { ...emptyPrefs(), ai: ON };
    mount();
    expect(within(ai()).getByTestId("ai-state").textContent).toMatch(
      /^What is sent has changed since you turned this on\. Review it and turn it on again\./);
    fireEvent.click(button("Review and turn on…"));
    fireEvent.click(within(dialog()).getByRole("button", { name: "Turn on" }));
    expect(prefs.setAi.mock.calls[0][0]).toMatchObject({ enabled: true, consent_policy: 3 });
  });
  it("stale too when the household names another service or more accounts: turning it on again agrees to them", () => {
    withStatus({ provider: "openai", endpoint_host: "127.0.0.1:11434", scope: "openai|127.0.0.1:11434|all" });
    prefs.doc = { ...emptyPrefs(), ai: ON };
    mount();
    expect(within(ai()).getByTestId("ai-state").textContent).toMatch(/^What is sent has changed since you turned this on/);
    fireEvent.click(button("Review and turn on…"));
    expect(dialog().textContent).toContain("Sent to the OpenAI-compatible service at 127.0.0.1:11434");
    fireEvent.click(within(dialog()).getByRole("button", { name: "Turn on" }));
    expect(prefs.setAi.mock.calls[0][0]).toMatchObject({ enabled: true, consent_policy: 2,
      consent_scope: "openai|127.0.0.1:11434|all" });
  });
  it("a store that cannot be reached: the switch says it is kept in this browser for now", () => {
    withStatus({});
    prefs.localOnly = true;
    mount();
    expect(ai().textContent).toContain("Store unreachable — saved in this browser only for now.");
  });
  it("with the status file missing it says it did not load, and offers no switch", () => {
    missing = new Set(["ai_status.json"]);
    mount();
    expect(within(ai()).getByTestId("missing-data").textContent).toMatch(/^The AI categorization status is unavailable/);
    expect(within(ai()).queryByRole("button")).toBeNull();
  });
});

describe("Settings → Elsewhere", () => {
  const links = () => within(card("card-elsewhere")).queryAllByRole("link").map((a) => [a.textContent, a.getAttribute("href")]);
  const ALL = [
    ["Privacy mode", "/data?card=privacy-mode"],
    ["Projection defaults", "/data?card=projection-defaults"],
    ["Saved views and reports", "/data?card=saved-views"],
    ["Budget", "/cash-flow/budget?card=budget"],
    ["Target mix", "/accounts/investments?card=target-mix"],
  ];
  it("links each setting on another page to its card, with the page it is on", () => {
    mount();
    expect(links()).toEqual(ALL);
    const items = [...card("card-elsewhere").querySelectorAll("li")].map((li) => li.textContent);
    expect(items[0]).toBe("Privacy mode · DataHides every dollar amount on this device.");
    expect(items[3]).toMatch(/^Budget · Cash Flow · Budget/);
    expect(screen.queryByTestId("elsewhere-later")).toBeNull();
  });
  it("with no data yet it links only what can be set already, and says the rest come with data, in both first-run states", () => {
    for (const state of [waitingData(), setupData()]) {
      cleanup();
      data = state;
      mount();
      expect(links(), "privacy and the projection defaults only").toEqual(ALL.slice(0, 2));
      expect(screen.getByTestId("elsewhere-later").textContent)
        .toBe("Saved views and reports, Budget and Target mix appear here once the dashboard has data.");
    }
  });
  it("shows no link into a page that is switched off (GatedLink's rule)", () => {
    render(<MemoryRouter><RefreshProvider>
      <OpenRoutesProvider value={(path) => !path.startsWith("/data")}><Settings /></OpenRoutesProvider>
    </RefreshProvider></MemoryRouter>);
    expect(links()).toEqual(ALL.slice(3));
  });
});

describe("Settings → About", () => {
  it("names the product, the build, the export's schema version and when the data was built, and the promise", () => {
    mount();
    const c = card("card-about");
    const rows = [...c.querySelectorAll("dl > div")].map((d) => [d.querySelector("dt")?.textContent, d.querySelector("dd")?.textContent]);
    expect(rows).toEqual([
      ["Product", "Family Financials"],
      ["Build", "development build"],
      ["Export schema", `version ${base.manifest.schema_version}`],
      // on the household's clock (its zone, America/New_York), with its abbreviation
      ["Data last built", fmtDateTime("2026-06-10T09:30:00-04:00", "America/New_York")],
      ["Copyright", COPYRIGHT],
      ["Licence", "Free software under the GNU AGPL 3.0, with no warranty"],
      ["Source code", "github.com/Good-Work-Dojo/Family-Financials"],
    ]);
    expect(rows[3][1]).toMatch(/^Jun 10, .* EDT$/);
    expect(c.querySelector("code"), "no pill for a build that names no bundle").toBeNull();
    expect(c.textContent).toContain("The dashboard makes no outside request: your data goes nowhere else.");
  });
  it("while AI categorization is on, says the vendor names go to its provider, linking to its card", () => {
    data = { ...base, aiStatus: { ...AI_STATUS_NONE, configured: true, enabled: true, provider: "openai", model: "m",
      policy_version: 2, consent_policy: 2 } };
    mount();
    const c = card("card-about");
    expect(c.textContent).not.toContain("your data goes nowhere else");
    expect(c.textContent).toContain("the names of vendors your rules have not sorted go to OpenAI for sorting");
    expect(within(c).getByRole("link", { name: "AI categorization" }).getAttribute("href"))
      .toBe("/settings?card=ai-categorization");
  });
  it("switched on with no key stored, says the vendor names will go to its provider once one is", () => {
    data = { ...base, aiStatus: { ...AI_STATUS_NONE, configured: false, enabled: true, provider: "anthropic",
      model: "m", policy_version: 2, consent_policy: 2 } };
    mount();
    const c = card("card-about");
    expect(c.textContent).toContain("the names of vendors your rules have not sorted will go to Anthropic for sorting "
      + "once a key is stored. Nothing else is sent.");
  });
  it("says whose the program is, that it is free software with no warranty, and links to its source", () => {
    mount();
    const c = card("card-about");
    expect(COPYRIGHT).toMatch(/^© 2026 \S.*\S$/);
    expect(COPYRIGHT, "a name, never an address").not.toContain("@");
    expect(LICENCE_LINE).toBe("Free software under the GNU AGPL 3.0, with no warranty");
    const link = within(c).getByRole("link", { name: "github.com/Good-Work-Dojo/Family-Financials" });
    expect(link.getAttribute("href"), "the repository, as the build left it").toBe(DEFAULT_SOURCE_URL);
    expect(DEFAULT_SOURCE_URL).toBe("https://github.com/Good-Work-Dojo/Family-Financials");
    expect(link.getAttribute("target"), "it opens beside the dashboard").toBe("_blank");
    expect(link.getAttribute("rel"), "the opened page gets no handle on this one and no referrer").toBe("noopener noreferrer");
  });
  it("prints a real build's id in a code pill", () => {
    render(<MemoryRouter><AboutCard build="0123456789abcdef" /></MemoryRouter>);
    expect(card("card-about").querySelector("dd code")?.textContent).toBe("0123456789abcdef");
  });
});

describe("Settings → Sign out", () => {
  const QUESTION = "You have changes that have not been saved yet. Sign out anyway?";
  /** the store's sign-out answers `status` (a function: throws, as an unreachable server does); every call to it is kept
   *  (the page's other cards read the store too, and are answered 404) */
  const logout = (status: number | (() => never) = 204) => {
    const calls: { url: string; method: string }[] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (!String(url).startsWith("/auth/")) return new Response("{}", { status: 404 });
      calls.push({ url: String(url), method: init?.method ?? "GET" });
      if (typeof status === "function") status();
      return new Response(null, { status: status as number });
    });
    return calls;
  };
  const seedMirrors = () => {
    for (const key of FAMILY_MIRRORS) localStorage.setItem(key, "{}");
    localStorage.setItem("theme", "light");
  };
  const button = (name: string) => within(card("card-sign-out")).getByRole("button", { name });
  beforeEach(() => { resetSession(); localStorage.clear(); });
  afterEach(() => vi.restoreAllMocks());

  it("an enabled button that, with nothing unsent, signs out at once: the store's sign-out, the copies forgotten, the sign-in page", async () => {
    seedMirrors();
    const calls = logout();
    const away = vi.spyOn(leave, "toSignIn").mockImplementation(() => {});
    mount();
    const b = button("Sign out") as HTMLButtonElement;
    expect(b.disabled || b.getAttribute("aria-disabled") === "true", "the control works").toBe(false);
    expect(card("card-sign-out").textContent).toContain(
      "Ends the sign-in on this browser and clears the copy of the family's answers, settings, scenarios and assets it keeps.");
    fireEvent.click(b);
    await waitFor(() => expect(away).toHaveBeenCalledTimes(1));
    expect(calls).toEqual([{ url: "/auth/logout", method: "POST" }]);
    for (const key of FAMILY_MIRRORS) expect(localStorage.getItem(key), key).toBeNull();
    expect(localStorage.getItem("theme"), "this device's own settings stay").toBe("light");
    expect(screen.queryByText(QUESTION), "nothing unsent: nothing to ask").toBeNull();
  });

  it("with unsent edits it asks first, focused on staying; staying sends nothing and keeps everything", async () => {
    seedMirrors();
    const calls = logout();
    const away = vi.spyOn(leave, "toSignIn").mockImplementation(() => {});
    reportUnsent(Symbol("prefs"), true);
    mount();
    fireEvent.click(button("Sign out"));
    expect(screen.getByText(QUESTION)).toBeDefined();
    expect(document.activeElement, "the safe answer has the focus").toBe(button("Stay signed in"));
    fireEvent.click(button("Stay signed in"));
    expect(screen.queryByText(QUESTION)).toBeNull();
    expect(document.activeElement, "focus goes back to the control").toBe(button("Sign out"));
    expect(calls).toEqual([]);
    expect(away).not.toHaveBeenCalled();
    for (const key of FAMILY_MIRRORS) expect(localStorage.getItem(key), key).not.toBeNull();
  });

  it("with unsent edits, signing out anyway signs out", async () => {
    seedMirrors();
    const calls = logout();
    const away = vi.spyOn(leave, "toSignIn").mockImplementation(() => {});
    reportUnsent(Symbol("answers"), true);
    mount();
    fireEvent.click(button("Sign out"));
    fireEvent.click(button("Sign out anyway"));
    await waitFor(() => expect(away).toHaveBeenCalledTimes(1));
    expect(calls).toEqual([{ url: "/auth/logout", method: "POST" }]);
    for (const key of FAMILY_MIRRORS) expect(localStorage.getItem(key), key).toBeNull();
  });

  it("a sign-out that does not go through says so and stays, the copies kept", async () => {
    for (const status of [403, 502, () => { throw new TypeError("Failed to fetch"); }] as const) {
      cleanup();
      seedMirrors();
      logout(status);
      const away = vi.spyOn(leave, "toSignIn").mockImplementation(() => {});
      mount();
      fireEvent.click(button("Sign out"));
      const said = await within(card("card-sign-out")).findByRole("alert");
      expect(said.textContent).toBe("Sign-out did not go through, so this browser is still signed in. Try again.");
      expect(away).not.toHaveBeenCalled();
      for (const key of FAMILY_MIRRORS) expect(localStorage.getItem(key), key).not.toBeNull();
      expect((button("Sign out") as HTMLButtonElement).disabled, "it can be pressed again").toBe(false);
      vi.restoreAllMocks();
    }
  });
});

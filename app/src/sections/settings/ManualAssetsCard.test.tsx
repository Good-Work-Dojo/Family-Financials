// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
// Settings → Manual assets: the editor and the after-save loop, with the family store (/api/manual-assets) and the
// refresh view (/api/refresh) answered at fetch, and the exports a hand-built household whose reload the test hands in.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AppData, ManualAsset } from "../../data/types";
import { acctFactory, appDataFactory, snapFactory } from "../../lib/__fixtures__";
import { FOLLOW_GRACE_MS, FOLLOW_MS } from "../../lib/ingestFollow";
import { type LiveAsset, MANUAL_ASSETS_LOCAL_KEY, type ManualAssetsDoc, mergeManualAssets } from "../../lib/manualAssets";
import { MASK, setPrivate } from "../../lib/privacy";
import { textHits } from "../../test/moneyScan";

const live = vi.hoisted(() => ({
  data: null as unknown as AppData,
  next: null as AppData | null,
  missing: new Set<string>(),
  listeners: new Set<() => void>(),
  reloads: 0,
}));
vi.mock("../../data/DataContext", async () => {
  const { useSyncExternalStore } = await import("react");
  const subscribe = (cb: () => void) => { live.listeners.add(cb); return () => { live.listeners.delete(cb); }; };
  return {
    useData: () => {
      const data = useSyncExternalStore(subscribe, () => live.data);
      return {
        data, missing: live.missing, indexes: { dataRange: { first: "2024-07-01", last: "2026-10-31" } },
        reload: async () => {
          live.reloads += 1;
          if (live.next) { live.data = live.next; live.next = null; live.listeners.forEach((l) => l()); }
        },
      };
    },
  };
});
import { RefreshProvider } from "../../components/DataRefresh";
import { ManualAssetsDraftProvider } from "../../data/useManualAssetsDraft";
import ManualAssetsCard from "./ManualAssetsCard";


const STAMP = "2026-09-01T16:00:00.000Z";
const G = "2026-10-31T18:00:00-04:00";
const HOME: LiveAsset = { id: "family-home", name: "Home", owner: "Joint", asset_class: "House", liquid: false,
  values: [{ as_of: "2024-07-01", value: 590000 }, { as_of: "2025-10-01", value: 615000 }, { as_of: "2026-09-01", value: 640000 }],
  updated_at: STAMP };
const exportOf = (a: LiveAsset): ManualAsset => ({ id: a.id, name: a.name, institution: null, owner: a.owner,
  asset_class: a.asset_class, liquid: a.liquid, acquired: a.acquired ?? null,
  values: a.values.map((v) => ({ as_of: v.as_of, value: v.value! })), origin: "app" });
const SEDAN: ManualAsset = { id: "sedan", name: "Sedan", institution: "Example Motors", owner: "Sam", asset_class: "Car",
  liquid: false, acquired: "2026-02-12", values: [{ as_of: "2026-03-26", value: 21000 }], origin: "config" };

const household = (over: Partial<AppData> = {}): AppData => {
  const base = appDataFactory(snapFactory({ snapshot_date: "2026-10-31", rows: [{ account: "Joint Checking", account_id: "chk",
    institution: "Bank", owner: "Joint", asset_class: "Cash", value: 1000, liquid: true, as_of: "2026-10-31", as_of_assumed: false,
    needs_review: false, source: "simplefin_balance", manual_id: null, carried_back: false }] }))();
  return { ...base, manifest: { ...base.manifest, generated_at: G }, accounts: [acctFactory()({ name: "Joint Checking" })],
    manualAssets: [exportOf(HOME), SEDAN], ...over };
};
const STORED = (): ManualAssetsDoc => ({ version: 1, updated_at: STAMP, assets: [HOME] });

interface Call { method: string; name: string; body: unknown }
type Reply = { http: number; body: unknown };
/** the family store and the refresh view: the store merges a PUT by id and echoes it, or answers `put` while it is set
 *  (a refusal); the refresh view answers from `refresh`, keeping its last entry, or from a function of the request's
 *  number (a reply that never comes is a promise that never settles) */
function serve({ stored = STORED(), refresh = [IDLE] as Reply[] | ((n: number) => Reply | null), put = null as Reply | null,
  down = false } = {}) {
  const s = { stored, calls: [] as Call[], refresh: Array.isArray(refresh) ? [...refresh] : refresh, put, reads: 0 };
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const name = String(url).replace(/^\.\//, "");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    s.calls.push({ method, name, body });
    if (name === "api/manual-assets") {
      if (down) throw new TypeError("network down");
      if (method === "PUT") {
        if (s.put) return new Response(JSON.stringify(s.put.body), { status: s.put.http });
        s.stored = mergeManualAssets(body as ManualAssetsDoc, s.stored, Date.now());
      }
      return new Response(JSON.stringify(s.stored), { status: 200 });
    }
    if (name === "api/refresh") {
      s.reads += 1;
      const step = typeof s.refresh === "function" ? s.refresh(s.reads)
        : s.refresh.length > 1 ? s.refresh.shift()! : s.refresh[0];
      if (step === null) return new Promise<Response>(() => {});
      return new Response(JSON.stringify(step.body), { status: step.http });
    }
    return new Response("not found", { status: 404 });
  });
  return s;
}
const view = (ingest: Record<string, unknown>): Reply => ({ http: 200, body: { state: "idle", trigger: null,
  request_nonce: null, queued_nonce: null, started_at: null, finished_at: null, rc: null, steps_failed: [], warnings: [],
  can_refresh: true, reason: "ok", retry_after_s: 0, used_today: 0, limit_per_day: 6, last_refresh_at: null,
  schedule: null, ingest: { wanted: false, last_started_at: "2026-10-31T10:00:00Z", last_finished_at: "2026-10-31T10:00:40Z",
    last_rc: 0, ...ingest } } });
const IDLE = view({});
const WANTED = view({ wanted: true });
const RUNNING = view({ last_started_at: "2026-10-31T22:02:00Z", last_finished_at: null, last_rc: null });
const ENDED = (rc: number) => view({ last_started_at: "2026-10-31T22:02:00Z", last_finished_at: "2026-10-31T22:02:40Z", last_rc: rc });

const settle = async (ms = 0) => {
  for (let i = 0; i < 6; i += 1) await act(async () => { await vi.advanceTimersByTimeAsync(i === 0 ? ms : 0); });
};
/** the card as the app holds it: inside the session's draft and the shared refresh; `shown` false is another page */
const tree = (shown = true) => (
  // the shared refresh's own looks at the status are off, so the refresh reads each test counts and scripts are the
  // card's follow's
  <MemoryRouter><RefreshProvider looks={false}><ManualAssetsDraftProvider>{shown ? <ManualAssetsCard /> : <p>another page</p>}
  </ManualAssetsDraftProvider></RefreshProvider></MemoryRouter>);
async function mount() {
  const r = render(tree());
  await settle();
  return r;
}
const card = () => screen.getByTestId("card-manual-assets");
const row = (id: string) => screen.getByTestId(`manual-asset-${id}`);
const status = () => screen.getByTestId("manual-assets-status").textContent;
const summary = () => screen.getByTestId("manual-assets-summary").textContent;
const saveButton = () => within(card()).getByRole("button", { name: "Save" });
const puts = (s: { calls: Call[] }) => s.calls.filter((c) => c.method === "PUT").map((c) => c.body as ManualAssetsDoc);
const refreshReads = (s: { calls: Call[] }) => s.calls.filter((c) => c.name === "api/refresh").length;
/** types an amount the way a person does: focus, type, leave */
function typeAmount(box: HTMLElement, text: string) {
  fireEvent.focus(box);
  fireEvent.change(box, { target: { value: text } });
  fireEvent.blur(box);
}
const pick = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
/** a new asset filled in: a car of Alex's worth 18,000 today (its fields found from its name box, which has focus) */
async function addCar(name = "Family car") {
  fireEvent.click(screen.getByRole("button", { name: "Add an asset" }));
  const box = document.activeElement as HTMLInputElement;
  const fields = within(box.closest("fieldset")!);
  fireEvent.change(box, { target: { value: name } });
  fireEvent.change(fields.getByLabelText("Owner"), { target: { value: "Alex" } });
  fireEvent.change(fields.getByLabelText("Class"), { target: { value: "Car" } });
  typeAmount(fields.getByLabelText("Value 1"), "18000");
}
async function save() {
  fireEvent.click(saveButton());
  await settle();
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-31T22:00:00Z"));   // 6 PM on Oct 31 in the household's zone
  localStorage.clear();
  sessionStorage.clear();
  live.data = household();
  live.next = null;
  live.missing = new Set();
  live.reloads = 0;
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); setPrivate(false); });

describe("Manual assets: the list", () => {
  it("shows the app asset and the config entry truthfully, with nothing to save", async () => {
    serve();
    await mount();
    expect(screen.getByTestId("manual-assets-count").textContent).toBe("2 hand-entered assets");
    expect(row("family-home").textContent).toContain("House · Joint · entered in this dashboard");
    expect(row("family-home").textContent).toContain("$640,000 as of Sep 1, 2026");
    expect(row("family-home").textContent, "saved and counted: no note").not.toMatch(/not in the dashboard|Left out/);
    const sedan = row("sedan");
    expect(sedan.textContent).toContain("Car · Sam · from your config");
    expect(sedan.textContent).toContain("$21,000 as of Mar 26, 2026");
    expect(sedan.textContent).toContain("Acquired Feb 12, 2026.");
    expect(sedan.textContent).toContain("To delete it, edit the household's config (manual_entries: in accounts.yaml).");
    expect(within(sedan).getByRole("button", { name: "Edit a copy of Sedan" })).toBeDefined();
    expect(within(sedan).queryByRole("button", { name: /Remove/ }), "a config entry cannot be removed here").toBeNull();
    expect(saveButton().getAttribute("aria-disabled")).toBe("true");
    expect(summary()).toBe("No unsaved changes.");
    expect(status()).toBe("");
  });

  it("with none, says so and offers the form", async () => {
    live.data = household({ manualAssets: [] });
    serve({ stored: { version: 1, updated_at: "1970-01-01T00:00:00Z", assets: [] } });
    await mount();
    expect(screen.getByTestId("empty-state").textContent)
      .toBe("None yet. Add one here, or state one in the household's config (manual_entries: in accounts.yaml).");
    fireEvent.click(screen.getByRole("button", { name: "Add an asset" }));
    expect(document.activeElement, "focus goes to the new asset's name").toBe(screen.getByLabelText("Name"));
  });

  it("while the family store cannot be reached, lists what the last ingest counted and changes nothing", async () => {
    serve({ down: true });
    await mount();
    expect(card().textContent).toContain("The family store is unreachable, so the assets cannot be changed right now.");
    expect(row("family-home").textContent).toContain("entered in this dashboard");
    expect(within(card()).queryByRole("button", { name: "Add an asset" })).toBeNull();
    expect(within(card()).queryByRole("button", { name: "Save" })).toBeNull();
  });
});

describe("Manual assets: editing", () => {
  it("adds a car: every field labelled, each problem tied to its field and summarised by Save until it is fixed", async () => {
    const s = serve();
    await mount();
    fireEvent.click(screen.getByRole("button", { name: "Add an asset" }));
    expect(row(card().querySelector("[data-testid^='manual-asset-a-']")!.getAttribute("data-testid")!.slice(13)).textContent)
      .toContain("not saved");
    expect(saveButton().getAttribute("aria-disabled")).toBe("true");
    expect(summary()).toBe("Unsaved changes · 4 problems to fix before saving:The new asset: Give it a name."
      + "The new asset: Choose who owns it.The new asset: Choose a class.The new asset: Enter the value.");
    const name = screen.getByLabelText("Name");
    // a field's error waits until the field is left or Save is pressed: the new form is not red before anything is typed
    expect(name.getAttribute("aria-invalid"), "untouched: no error yet").toBeNull();
    expect(card().querySelector("fieldset")!.querySelectorAll(".text-red-400"), "no field error shows on the fresh form")
      .toHaveLength(0);
    fireEvent.blur(name);
    expect(name.getAttribute("aria-invalid"), "left empty: its error").toBe("true");
    expect(document.getElementById(name.getAttribute("aria-describedby")!)?.textContent).toBe("Give it a name.");
    expect(screen.getByLabelText("Owner").getAttribute("aria-invalid"), "the owner is not touched yet").toBeNull();
    expect(document.getElementById(saveButton().getAttribute("aria-describedby")!)?.textContent).toBe(summary());
    for (const label of ["Owner", "Class", "Acquired on (optional)", "Liquid: it can be spent soon", "Date of value 1", "Value 1"]) {
      expect(screen.getByLabelText(label), label).toBeDefined();
    }
    fireEvent.click(saveButton());
    await settle();
    expect(puts(s), "a draft with problems is not sent").toHaveLength(0);
    // Save pressed: every field shows its error, untouched ones included
    for (const label of ["Owner", "Class", "Value 1"]) {
      expect(screen.getByLabelText(label).getAttribute("aria-invalid"), `${label} after Save`).toBe("true");
    }

    fireEvent.change(name, { target: { value: "Family car" } });
    pick("Owner", "Alex");
    pick("Class", "Car");
    typeAmount(screen.getByLabelText("Value 1"), "18000");
    expect(summary()).toBe("Unsaved changes.");
    expect(saveButton().getAttribute("aria-disabled")).toBeNull();
    await save();
    const [sent] = puts(s);
    const car = sent.assets.find((a) => a.name === "Family car") as LiveAsset;
    expect(car.id).toMatch(/^a-[0-9a-f]{12}$/);
    expect(car).toEqual({ id: car.id, name: "Family car", owner: "Alex", asset_class: "Car", liquid: false, acquired: null,
      values: [{ as_of: "2026-10-31", value: 18000 }], updated_at: "2026-10-31T22:00:00.000Z" });
    expect(sent.assets.find((a) => a.id === "family-home"), "the home goes as stored").toEqual(HOME);
    expect(summary()).toBe("No unsaved changes.");
  });

  it("the count is of the saved assets: a new one being filled in is not one until it is saved", async () => {
    const s = serve();
    await mount();
    expect(screen.getByTestId("manual-assets-count").textContent).toBe("2 hand-entered assets");
    fireEvent.click(screen.getByRole("button", { name: "Add an asset" }));
    expect(screen.getByTestId("manual-assets-count").textContent, "a blank draft is no asset").toBe("2 hand-entered assets");
    cleanup();
    serve({ stored: s.stored });
    await mount();
    await addCar();
    await save();
    expect(screen.getByTestId("manual-assets-count").textContent, "saved, it counts").toBe("3 hand-entered assets");
  });

  it("the class's colour sits beside its label, so the class box lines up with the other boxes", async () => {
    serve();
    await mount();
    fireEvent.click(screen.getByRole("button", { name: "Add an asset" }));
    const box = screen.getByLabelText("Class");
    expect(box.parentElement, "the box is the field's own child, as the name's and the owner's are")
      .toBe(screen.getByLabelText("Owner").parentElement!.parentElement!.children[2]);
    expect(screen.queryByTestId("class-swatch"), "no class chosen: no colour").toBeNull();
    fireEvent.change(box, { target: { value: "Car" } });
    expect(box.closest("div")!.querySelector("label [data-testid=class-swatch]")).not.toBeNull();
  });

  it("the acquired question: a first value after the data begins asks, and 'acquired on that day' sets it", async () => {
    serve();
    await mount();
    await addCar();
    const q = screen.getByTestId("acquired-question");
    expect(q.textContent).toBe("Owned it before Jul 1, 2024? Nothing to do: it is counted at its first value back to then."
      + "Acquired on Oct 31, 2026? Set Oct 31, 2026 as the day it was acquired");
    fireEvent.click(within(q).getByRole("button", { name: "Set Oct 31, 2026 as the day it was acquired" }));
    expect((screen.getByLabelText("Acquired on (optional)") as HTMLInputElement).value).toBe("2026-10-31");
    expect(screen.queryByTestId("acquired-question")).toBeNull();
  });

  it("adds a value to the home, and a value dated ahead says when it takes effect", async () => {
    const s = serve();
    await mount();
    fireEvent.click(within(row("family-home")).getByRole("button", { name: "Edit Home" }));
    expect(screen.queryByTestId("acquired-question"), "the home's first value is the data's first day").toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Add a value" }));
    fireEvent.change(screen.getByLabelText("Date of value 4"), { target: { value: "2026-11-20" } });
    typeAmount(screen.getByLabelText("Value 4"), "650000");
    expect(row("family-home").textContent).toContain("Takes effect on Nov 20, 2026.");
    expect(row("family-home").textContent).toContain(
      "Dated after the accounts' newest report (Oct 31, 2026): it counts once the accounts report on or after Nov 20, 2026.");
    expect(row("family-home").textContent).toContain("unsaved");
    await save();
    const home = puts(s)[0].assets.find((a) => a.id === "family-home") as LiveAsset;
    expect(home.values.at(-1)).toEqual({ as_of: "2026-11-20", value: 650000 });
    expect(home.values).toHaveLength(4);
    expect(home.updated_at).toBe("2026-10-31T22:00:00.000Z");
  });

  it("a new asset's first value dated today counts at once, though the accounts last reported a month ago", async () => {
    // a file-only household: its newest balance is the file's, a month before today
    const fileOnly = household();
    const snap = fileOnly.assets.snapshots[0];
    live.data = { ...fileOnly, assets: { snapshots: [{ ...snap, snapshot_date: "2026-09-30",
      rows: snap.rows.map((r) => ({ ...r, as_of: "2026-09-30", source: "bank_balance" })) }] } };
    serve();
    await mount();
    await addCar();
    const car = screen.getByLabelText("Name").closest("li")!;
    expect(car.textContent, "no wait for the accounts' next report").not.toContain("counts once the accounts report");
    expect(car.textContent, "dated today: nothing about a later day").not.toMatch(/Takes effect|Counted at once/);
  });

  it("a new asset's first value dated ahead counts at once, and says so in place of a day it takes effect", async () => {
    serve();
    await mount();
    await addCar();
    fireEvent.change(screen.getByLabelText("Date of value 1"), { target: { value: "2027-01-01" } });
    const car = screen.getByLabelText("Name").closest("li")!;
    expect(car.textContent).toContain("Counted at once: an asset counts at its first value until a later one takes effect.");
    expect(car.textContent).not.toContain("Takes effect on");
    expect(car.textContent).not.toContain("counts once the accounts report");
  });

  it("removes an asset: a row says what happens, focus goes to Revert, and the save carries a tombstone", async () => {
    const s = serve();
    await mount();
    fireEvent.click(within(row("family-home")).getByRole("button", { name: "Remove Home" }));
    expect(within(row("family-home")).getByTestId("manual-asset-removal").textContent)
      .toBe("Removed — once saved, it leaves the dashboard after the next ingest.");
    expect(within(row("family-home")).queryByRole("button"), "nothing to edit on a removal").toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Revert" }));
    expect(summary()).toBe("Unsaved changes.");
    await save();
    expect(puts(s)[0].assets).toEqual([{ id: "family-home", name: "Home", updated_at: "2026-10-31T22:00:00.000Z",
      deleted_at: "2026-10-31T22:00:00.000Z" }]);
    expect(within(row("family-home")).getByTestId("manual-asset-removal").textContent,
      "saved, and the exports still count it").toBe("Removed — it leaves the dashboard after the next ingest.");
    // the documents alone give the row: a reload shows it too
    cleanup();
    serve({ stored: s.stored });
    await mount();
    expect(within(row("family-home")).getByTestId("manual-asset-removal").textContent)
      .toBe("Removed — it leaves the dashboard after the next ingest.");
  });

  it("Revert drops the draft; Save and Revert say nothing changed, and nothing is sent", async () => {
    const s = serve();
    await mount();
    fireEvent.click(within(row("family-home")).getByRole("button", { name: "Edit Home" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "The house" } });
    expect(summary()).toBe("Unsaved changes.");
    fireEvent.click(screen.getByRole("button", { name: "Revert" }));
    expect(row("family-home").textContent).toContain("Home");
    expect(summary()).toBe("No unsaved changes.");
    expect(screen.getByRole("button", { name: "Revert" }).getAttribute("aria-disabled")).toBe("true");
    expect(saveButton().getAttribute("aria-disabled")).toBe("true");
    await settle(5000);
    expect(puts(s)).toHaveLength(0);
  });

  it("Edit a copy makes an app asset of the entry's id that overrides it; Use the config's value again undoes it", async () => {
    const s = serve();
    await mount();
    fireEvent.click(screen.getByRole("button", { name: "Edit a copy of Sedan" }));
    const copy = row("sedan");
    expect(copy.textContent).toContain("Car · Sam · your copy of the entry in your config");
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Sedan");
    expect((screen.getByLabelText("Acquired on (optional)") as HTMLInputElement).value).toBe("2026-02-12");
    fireEvent.click(within(copy).getByRole("button", { name: "Use the config's value again for Sedan" }));
    expect(row("sedan").textContent).toContain("from your config");
    expect(summary(), "a copy undone before a save is no change").toBe("No unsaved changes.");

    fireEvent.click(screen.getByRole("button", { name: "Edit a copy of Sedan" }));
    typeAmount(screen.getByLabelText("Value 1"), "19500");
    expect(row("sedan").textContent, "the copy keeps no holder").toContain(
      "The config's own value: Sedan, $21,000. A copy keeps no holder: where the config names Example Motors, the copy reads as entered by hand.");
    await save();
    const sent = puts(s)[0].assets.find((a) => a.id === "sedan") as LiveAsset;
    expect(sent, "the document carries no mark: the exports say it is a copy").toEqual({ id: "sedan", name: "Sedan",
      owner: "Sam", asset_class: "Car", liquid: false, acquired: "2026-02-12",
      values: [{ as_of: "2026-03-26", value: 19500 }], updated_at: "2026-10-31T22:00:00.000Z" });
    expect(row("sedan").textContent, "saved, not ingested yet: still a copy").toContain("your copy of the entry in your config");
  });

  it("a copy saved and ingested: the hidden entry is named with its value, and taking it back says so until the ingest", async () => {
    const copy: LiveAsset = { id: "sedan", name: "Sedan", owner: "Sam", asset_class: "Car", liquid: false,
      acquired: "2026-02-12", values: [{ as_of: "2026-03-26", value: 19500 }], updated_at: STAMP };
    live.data = household({ manualAssets: [exportOf(HOME), exportOf(copy)], manualOverridden: [SEDAN] });
    const s = serve({ stored: { ...STORED(), assets: [HOME, copy] } });
    await mount();
    expect(row("sedan").textContent).toContain("Car · Sam · your copy of the entry in your config");
    expect(row("sedan").textContent).toContain("The config's own value: Sedan, $21,000.");
    fireEvent.click(within(row("sedan")).getByRole("button", { name: "Use the config's value again for Sedan" }));
    expect(within(row("sedan")).getByTestId("manual-asset-removal").textContent)
      .toBe("Once saved, the config's value (Sedan, $21,000) counts again after the next ingest.");
    await save();
    expect(puts(s)[0].assets.find((a) => a.id === "sedan")).toEqual({ id: "sedan", name: "Sedan",
      updated_at: "2026-10-31T22:00:00.000Z", deleted_at: "2026-10-31T22:00:00.000Z" });
    expect(within(row("sedan")).getByTestId("manual-asset-removal").textContent)
      .toBe("The config's value (Sedan, $21,000) counts again after the next ingest.");
  });

  it("the config entry since removed from accounts.yaml: the asset is its own, removed with Remove", async () => {
    const copy: LiveAsset = { id: "sedan", name: "Sedan", owner: "Sam", asset_class: "Car", liquid: false,
      values: [{ as_of: "2026-03-26", value: 19500 }], updated_at: STAMP };
    live.data = household({ manualAssets: [exportOf(HOME), exportOf(copy)], manualOverridden: [] });
    serve({ stored: { ...STORED(), assets: [HOME, { ...copy, from_config: true } as LiveAsset] } });
    await mount();
    expect(row("sedan").textContent).toContain("Car · Sam · entered in this dashboard");
    expect(within(row("sedan")).getByRole("button", { name: "Remove Sedan" })).toBeDefined();
    expect(within(row("sedan")).queryByRole("button", { name: /config's value/ }), "no way back to nothing").toBeNull();
  });

  it("a copy the engine left out is still a copy, with the engine's reason, and its problem does not block other saves", async () => {
    const copy: LiveAsset = { id: "sedan", name: "Sedan", owner: "Robin", asset_class: "Car", liquid: false,
      values: [{ as_of: "2026-03-26", value: 19500 }], updated_at: STAMP };
    live.data = household({ quality: { ...household().quality, checks: [{ name: "manual_assets.entry", status: "warn",
      detail: "manual asset 'sedan' from the app left out: its owner is not one of the household's owners; the "
        + "configuration's entry with this id counts instead" }] } });
    const s = serve({ stored: { ...STORED(), assets: [HOME, copy] } });
    await mount();
    expect(row("sedan").textContent).toContain("your copy of the entry in your config");
    expect(row("sedan").textContent).toContain("Left out of net worth by the last ingest: its owner is not one of the "
      + "household's owners; the configuration's entry with this id counts instead.");
    expect(row("sedan").textContent).toContain(
      "To fix (it does not stop other changes from being saved): Choose one of the household's owners.");
    await addCar();
    expect(summary(), "the untouched copy's problem is its own row's").toBe("Unsaved changes.");
    await save();
    expect(puts(s)).toHaveLength(1);
  });

  it("a debt copied with a figure above zero: the box shows it below zero and the problem blocks until it is typed again", async () => {
    const loan: ManualAsset = { ...SEDAN, id: "family-loan", name: "Family loan", asset_class: "Other Debt",
      values: [{ as_of: "2026-03-26", value: 5000 }] };
    live.data = household({ manualAssets: [exportOf(HOME), SEDAN, loan] });
    serve();
    await mount();
    fireEvent.click(screen.getByRole("button", { name: "Edit a copy of Family loan" }));
    const box = screen.getByLabelText("Amount owed 1") as HTMLInputElement;
    expect(box.value).toBe("-5,000");
    expect(summary()).toContain("Family loan: This is above zero, so net worth would count this debt as an asset: type the amount owed again.");
    typeAmount(box, "5000");
    expect(summary()).toBe("Unsaved changes.");
  });

  it("a debt: the amount is entered owed and stored negative, and it is never liquid", async () => {
    const s = serve();
    await mount();
    fireEvent.click(screen.getByRole("button", { name: "Add an asset" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Loan from family" } });
    pick("Owner", "Joint");
    pick("Class", "Other Debt");
    const liquid = screen.getByLabelText("Liquid: it can be spent soon") as HTMLInputElement;
    expect(liquid.disabled).toBe(true);
    expect(document.getElementById(liquid.getAttribute("aria-describedby")!)?.textContent).toBe("A debt is never liquid.");
    typeAmount(screen.getByLabelText("Amount owed 1"), "5000");
    await save();
    const loan = puts(s)[0].assets.find((a) => a.name === "Loan from family") as LiveAsset;
    expect(loan.values).toEqual([{ as_of: "2026-10-31", value: -5000 }]);
    expect(loan.liquid).toBe(false);
  });

  it("the classes come in the dashboard's order, assets and debts apart; the owners are the household's", async () => {
    serve();
    await mount();
    fireEvent.click(screen.getByRole("button", { name: "Add an asset" }));
    const groups = [...screen.getByLabelText("Class").querySelectorAll("optgroup")]
      .map((g) => [g.label, [...g.querySelectorAll("option")].map((o) => o.textContent)]);
    expect(groups).toEqual([
      ["Assets", ["Cash", "Stocks/ETF", "Retirement", "Crypto", "Precious Metals", "Real Estate/Venture", "House", "Car", "529", "Private Stock"]],
      ["Debts", ["Mortgage", "Margin", "Other Debt"]],
    ]);
    expect([...screen.getByLabelText("Owner").querySelectorAll("option")].map((o) => o.textContent))
      .toEqual(["Choose who owns it", "Alex", "Sam", "Joint"]);
  });

  it("privacy mode masks every amount, the open editor's boxes included", async () => {
    setPrivate(true);
    serve();
    await mount();
    fireEvent.click(within(row("family-home")).getByRole("button", { name: "Edit Home" }));
    expect(textHits(card()), "no amount shows").toEqual([]);
    expect((screen.getByLabelText("Value 1") as HTMLInputElement).value).toBe(MASK);
    expect(row("sedan").textContent).toContain(MASK);
  });
});

describe("Manual assets: after a save", () => {
  it("while the ingest is wanted or running: saved, the dashboard updates after the next ingest", async () => {
    serve({ refresh: [WANTED, RUNNING] });
    await mount();
    await addCar();
    await save();
    expect(status()).toBe("Saved — the dashboard updates after the next ingest.");
    await settle(3100);
    expect(status()).toBe("Saved — the dashboard updates after the next ingest.");
  });

  it("once the ingest has run and the exports are reloaded holding what was saved: up to date", async () => {
    const s = serve({ refresh: [WANTED, RUNNING, ENDED(0)] });
    await mount();
    await addCar();
    await save();
    const car = puts(s)[0].assets.find((a) => a.name === "Family car") as LiveAsset;
    live.next = household({ manifest: { ...live.data.manifest, generated_at: "2026-10-31T18:03:00-04:00" },
      manualAssets: [exportOf(car), exportOf(HOME), SEDAN] });
    await settle(3100);
    await settle(3100);
    expect(live.reloads, "the ingest's end reloads the exports once").toBe(1);
    expect(status()).toBe("Up to date: the dashboard counts what was saved.");
    expect(row(car.id).textContent).not.toMatch(/not in the dashboard|Left out|not saved|unsaved/);
  });

  it("an asset the ingest left out shows the engine's reason; one not in the exports says so", async () => {
    const s = serve({ refresh: [WANTED, ENDED(0)] });
    await mount();
    await addCar();
    await addCar("Boat");
    await save();
    const ids = Object.fromEntries(puts(s)[0].assets.map((a) => [a.name, a.id]));
    live.next = household({ manifest: { ...live.data.manifest, generated_at: "2026-10-31T18:03:00-04:00" },
      quality: { ...live.data.quality, checks: [{ name: "manual_assets.entry", status: "warn",
        detail: `manual asset '${ids["Family car"]}' from the app left out: its owner is not one of the household's owners` }] } });
    await settle(3100);
    expect(status()).toBe("The ingest ran, but the dashboard does not count everything saved yet: see the notes below.");
    expect(row(ids["Family car"]).textContent).toContain(
      "Left out of net worth by the last ingest: its owner is not one of the household's owners.");
    expect(row(ids.Boat).textContent).toContain("Saved; not in the dashboard yet: it counts after the next ingest.");
  });

  it("an ingest that failed says so with its code", async () => {
    serve({ refresh: [WANTED, ENDED(2)] });
    await mount();
    await addCar();
    await save();
    await settle(3100);
    expect(status()).toBe("Saved, but the last ingest failed (code 2). The dashboard still shows what it counted before.");
  });

  it("the follow ends at its bound: the next scheduled refresh or ingest shows it, and the polling stops", async () => {
    const s = serve({ refresh: [WANTED] });
    await mount();
    await addCar();
    await save();
    await settle(FOLLOW_MS + 100);
    expect(status()).toBe("Saved. The dashboard shows it after the next scheduled refresh or ingest.");
    const reads = refreshReads(s);
    expect(reads, "polled no faster than every 3 s").toBeLessThanOrEqual(Math.ceil(FOLLOW_MS / 3000) + 2);
    await settle(60_000);
    expect(refreshReads(s), "it never spins for ever").toBe(reads);
  });

  it("a save whose ingest was not asked for (nothing wanted after it) waits for the next refresh", async () => {
    serve({ refresh: [IDLE] });
    await mount();
    await addCar();
    await save();
    expect(status()).toBe("Saved. The dashboard shows it after the next scheduled refresh or ingest.");
  });

  it("with no refresh service (a household that runs the ingest by hand): re-run the ingest", async () => {
    serve({ refresh: [{ http: 503, body: { error: "refresh not configured" } }] });
    await mount();
    await addCar();
    await save();
    expect(status()).toBe("Saved. Re-run the ingest to see it.");
  });

  it("a tab that slept past the bound and wakes after the ingest ended looks once more, and sees the change counted", async () => {
    // the poll that went out before the tab slept never comes back; the look past the bound finds the ingest ended
    const s = serve({ refresh: (n) => (n === 1 ? WANTED : n === 2 ? null : ENDED(0)) });
    await mount();
    await addCar();
    await save();
    const car = puts(s)[0].assets.find((a) => a.name === "Family car") as LiveAsset;
    live.next = household({ manifest: { ...live.data.manifest, generated_at: "2026-10-31T18:05:00-04:00" },
      manualAssets: [exportOf(car), exportOf(HOME), SEDAN] });
    await settle(FOLLOW_MS + 100);
    expect(status()).toBe("Up to date: the dashboard counts what was saved.");
    await settle(FOLLOW_GRACE_MS + 1000);
    expect(status(), "the grace never turns an end into later").toBe("Up to date: the dashboard counts what was saved.");
  });

  it("the look past the bound never answers: the follow ends as later once its grace has passed", async () => {
    serve({ refresh: (n) => (n === 1 ? WANTED : null) });
    await mount();
    await addCar();
    await save();
    await settle(FOLLOW_MS + 100);
    expect(status()).toBe("Saved — the dashboard updates after the next ingest.");
    await settle(FOLLOW_GRACE_MS);
    expect(status()).toBe("Saved. The dashboard shows it after the next scheduled refresh or ingest.");
  });

  it("the look past the bound sees the ingest end and the reload never brings new exports: later once the grace has passed", async () => {
    serve({ refresh: (n) => (n === 1 ? WANTED : n === 2 ? null : ENDED(0)) });
    await mount();
    await addCar();
    await save();
    await settle(FOLLOW_MS + 100);
    expect(live.reloads, "the end seen reloads the exports").toBeGreaterThanOrEqual(1);
    expect(status()).toBe("Saved — the dashboard updates after the next ingest.");
    await settle(FOLLOW_GRACE_MS);
    expect(status()).toBe("Saved. The dashboard shows it after the next scheduled refresh or ingest.");
  });

  it("a refusal shows the store's reason, and the same document is never sent again", async () => {
    const s = serve({ put: { http: 400, body: { error: "at most 50 assets: combined with what the store already holds the document would have 51, so nothing was written" } } });
    await mount();
    await addCar();
    await save();
    expect(status()).toBe("Not saved: the family store refused it — at most 50 assets: combined with what the store "
      + "already holds the document would have 51, so nothing was written. Change what it names and save again.");
    await settle(120_000);
    expect(puts(s)).toHaveLength(1);
    expect(refreshReads(s), "nothing was saved, so no ingest is followed").toBe(0);
  });

  it("the store unreachable at the save: the change waits in this browser and is sent once the store answers", async () => {
    const s = serve();
    await mount();
    await addCar();
    vi.stubGlobal("fetch", async () => { throw new TypeError("network down"); });
    await save();
    expect(status()).toBe("Not saved yet: the family store is unreachable. The change stays in this browser and is sent "
      + "once the store answers.");
    expect(JSON.parse(localStorage.getItem(MANUAL_ASSETS_LOCAL_KEY) ?? "{}").assets.some((a: LiveAsset) => a.name === "Family car"))
      .toBe(true);
    serve({ stored: s.stored, refresh: [WANTED] });
    await settle(2100);
    expect(status()).toBe("Saved — the dashboard updates after the next ingest.");
  });
});

describe("Manual assets: a refused save is not a saved one", () => {
  const FULL_STORE = { http: 400, body: { error: "at most 50 assets: nothing was written" } };
  const carRow = () => screen.getAllByTestId(/^manual-asset-a-/)[0];

  it("after a refusal the asset is still not saved, and Revert puts the store's own copy back", async () => {
    const s = serve({ put: FULL_STORE });
    await mount();
    await addCar();
    await save();
    expect(status()).toBe("Not saved: the family store refused it — at most 50 assets: nothing was written. Change what it names and save again.");
    expect(carRow().textContent).toContain("not saved");
    expect(summary()).toBe("Unsaved changes.");
    expect(screen.getByRole("button", { name: "Revert" }).getAttribute("aria-disabled")).toBeNull();
    s.put = null;                                                    // the store would take a document again
    fireEvent.click(screen.getByRole("button", { name: "Revert" }));
    await settle();
    expect(screen.queryAllByTestId(/^manual-asset-a-/)).toHaveLength(0);
    expect(summary()).toBe("No unsaved changes.");
    expect(puts(s).at(-1), "the store's copy goes back, so the refused one is not sent again").toEqual(STORED());
    expect(status(), "the refusal is over").toBe("");
  });

  it("after a reload the refused change is still shown unsaved, with the refusal", async () => {
    serve({ put: FULL_STORE });
    await mount();
    await addCar();
    await save();
    cleanup();
    serve({ put: FULL_STORE });                                     // this browser's mirror still holds the change
    await mount();
    expect(status()).toBe("Not saved: the family store refused it — at most 50 assets: nothing was written. Change what it names and save again.");
    expect(carRow().textContent).toContain("not saved");
  });

  it("a later corrected save goes through", async () => {
    const s = serve({ put: FULL_STORE });
    await mount();
    await addCar();
    await save();
    s.put = null;
    fireEvent.click(within(carRow()).getByRole("button", { name: "Edit Family car" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "The car" } });
    await save();
    expect(status()).toBe("Saved. The dashboard shows it after the next scheduled refresh or ingest.");
    expect(s.stored.assets.some((a) => a.name === "The car")).toBe(true);
    expect(carRow().textContent).not.toMatch(/not saved|unsaved/);
  });

  it("a stamp refused as too far ahead says this device's clock looks wrong", async () => {
    serve({ put: { http: 400, body: { error: "'a-0123456789ab': an asset's timestamp is in the future: check this device's clock" } } });
    await mount();
    await addCar();
    await save();
    expect(status()).toBe("Not saved: this device's clock looks wrong — the family store found a time more than a day "
      + "ahead of its own. Correct the clock, then save again.");
  });
});

describe("Manual assets: the draft lasts the session", () => {
  it("typed, another page, back: the draft is there, still unsaved", async () => {
    serve();
    const { rerender } = await mount();
    fireEvent.click(within(row("family-home")).getByRole("button", { name: "Edit Home" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "The house" } });
    rerender(tree(false));
    await settle();
    expect(screen.queryByTestId("card-manual-assets")).toBeNull();
    rerender(tree());
    await settle();
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("The house");
    expect(summary()).toBe("Unsaved changes.");
  });

  it("an asset edited here and removed on another device meanwhile says so on its row", async () => {
    const s = serve();
    const { rerender } = await mount();
    fireEvent.click(within(row("family-home")).getByRole("button", { name: "Edit Home" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "The house" } });
    rerender(tree(false));
    s.stored = { ...s.stored, assets: [{ id: "family-home", name: "Home", updated_at: "2026-10-31T21:00:00.000Z",
      deleted_at: "2026-10-31T21:00:00.000Z" }] };
    rerender(tree());
    await settle();
    expect(row("family-home").textContent).toContain(
      "Removed on another device while you were editing it: saving brings it back with your changes.");
  });
});

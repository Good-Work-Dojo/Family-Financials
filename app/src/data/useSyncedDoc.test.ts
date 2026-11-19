// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  emptyPrefs, normalizePrefs, type PrefsDoc, withAi, withBudget, withModule, withoutSaved, withRenamedSaved, withSaved, withTargetMix,
} from "../lib/prefs";
import { useAnswersStore } from "./useAnswersStore";
import { PREFS_SYNC } from "./usePrefsStore";
import { canonicalJson, EPOCH_ISO, mergeById, RETRY_MAX_MS, retryDelay, useSyncedDoc } from "./useSyncedDoc";

/** The transport behind the answers and prefs stores, driven through a stubbed fetch. Each "device" is its
 *  own hook instance with its own local mirror (localKey), so two of them are two browsers sharing one store. */

type Json = Record<string, unknown>;
interface Call { method: string; body: Json | null; keepalive: boolean }

/** the sections the store keeps whole when a PUT omits them (deploy/scenario_store.py PREFS_KEPT_WHOLE), each an
 *  object when kept */
const STORE_KEEPS_WHOLE = ["target_mix", "modules", "ai"];
/** what the store keeps of `stored` when a PUT of `body` omits it: the lists and the sections kept whole */
function storeMerge(body: Json, stored: Json): Json {
  const merged: Json = { ...body };
  for (const k of ["saved_filters", "reports"]) if (!(k in body) && Array.isArray(stored[k])) merged[k] = stored[k];
  for (const k of STORE_KEEPS_WHOLE) if (!(k in body) && stored[k] && typeof stored[k] === "object") merged[k] = stored[k];
  return merged;
}

/** A family store for ./api/prefs that answers like deploy/scenario_store.py: GET returns the stored doc; PUT
 *  stores the incoming doc whole, except that a list section, the target mix, the modules or the AI consent the PUT
 *  omits keeps its stored copy (merge_prefs, pinned server-side by test_prefs_put_keeps_array_sections_an_old_bundle_omits,
 *  test_prefs_target_mix_is_validated_kept_when_omitted_and_cleared_by_null and test_an_old_tab_cannot_erase_modules_or_ai),
 *  and echoes what it stored. */
function prefsStore(initial: Json = { version: 1, updated_at: EPOCH_ISO }) {
  const s = { stored: initial, calls: [] as Call[] };
  vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) as Json : null;
    s.calls.push({ method, body, keepalive: !!init?.keepalive });
    if (method === "PUT" && body) s.stored = storeMerge(body, s.stored);
    return new Response(JSON.stringify(s.stored), { status: 200 });
  });
  return s;
}
const puts = (s: { calls: Call[] }) => s.calls.filter((c) => c.method === "PUT");

/** one browser on the prefs doc: the real prefs options, its own mirror, a short debounce */
const device = (localKey: string) =>
  renderHook(() => useSyncedDoc<PrefsDoc>({ ...PREFS_SYNC, localKey, debounceMs: 5 })).result;
async function loaded<R extends { current: { loading: boolean } }>(d: R): Promise<R> {
  await waitFor(() => expect(d.current.loading).toBe(false));
  return d;
}

const T = (min: number) => `2026-07-16T10:${String(min).padStart(2, "0")}:00.000Z`;
const view = (id: string) => ({ id, name: id, query: `q=${id}` });
const ids = (list: unknown) => (list as { id: string }[] | undefined)?.map((v) => v.id);

let visibility: DocumentVisibilityState = "visible";
beforeEach(() => {
  localStorage.clear();
  visibility = "visible";
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function comeBack() {
  visibility = "hidden";
  document.dispatchEvent(new Event("visibilitychange"));
  visibility = "visible";
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("prefs: a tab never writes back the lists it loaded over ones another device saved since", () => {
  it("B saves view V; A, loaded before V, then saves a budget: the store still holds V, and A now shows it", async () => {
    const store = prefsStore();
    const a = await loaded(device("dev-a"));
    const b = await loaded(device("dev-b"));
    act(() => b.current.commit((cur) => withSaved(cur, "saved_filters", view("V"), T(1))!));
    await waitFor(() => expect(ids(store.stored.saved_filters)).toEqual(["V"]));

    act(() => a.current.commit((cur) => withBudget(cur, "Groceries", 900, T(2))));
    await waitFor(() => expect(puts(store)).toHaveLength(2));
    expect(puts(store)[1].body, "the budget PUT carries no list A did not edit").not.toHaveProperty("saved_filters");
    expect(puts(store)[1].body).not.toHaveProperty("reports");
    expect(ids(store.stored.saved_filters), "V survived A's save").toEqual(["V"]);
    expect(store.stored.budgets).toEqual({ Groceries: 900 });
    // the echo hands A the list the store kept, so A's next list edit starts from it
    await waitFor(() => expect(ids(a.current.doc.saved_filters)).toEqual(["V"]));
    expect(a.current.doc.budgets).toEqual({ Groceries: 900 });
  });

  it("a list A did edit is sent (and wins, last-write-wins), so a deletion still reaches the store", async () => {
    const store = prefsStore({ ...emptyPrefs(), updated_at: T(0), saved_filters: [{ ...view("V"), created_at: T(0) }] });
    const a = await loaded(device("dev-a"));
    expect(ids(a.current.doc.saved_filters)).toEqual(["V"]);
    act(() => a.current.commit((cur) => ({ ...cur, updated_at: T(1), saved_filters: [] })));
    await waitFor(() => expect(puts(store)).toHaveLength(1));
    expect(puts(store)[0].body).toHaveProperty("saved_filters", []);
    expect(store.stored.saved_filters).toEqual([]);
  });

  it("coming back to the tab re-reads the doc, so a list edit made afterwards keeps the view another device saved", async () => {
    const store = prefsStore();
    const a = await loaded(device("dev-a"));
    const b = await loaded(device("dev-b"));
    act(() => b.current.commit((cur) => withSaved(cur, "saved_filters", view("V"), T(1))!));
    await waitFor(() => expect(ids(store.stored.saved_filters)).toEqual(["V"]));
    expect(ids(a.current.doc.saved_filters), "A has not heard of V yet").toEqual([]);

    comeBack();
    await waitFor(() => expect(ids(a.current.doc.saved_filters)).toEqual(["V"]));
    act(() => a.current.commit((cur) => withSaved(cur, "saved_filters", view("W"), T(2))!));
    await waitFor(() => expect(ids(store.stored.saved_filters)).toEqual(["V", "W"]));
  });

  it("the re-read waits while an edit is still waiting to be sent, and a hidden tab does not re-read", async () => {
    const store = prefsStore();
    const a = renderHook(() => useSyncedDoc<PrefsDoc>({ ...PREFS_SYNC, localKey: "dev-a", debounceMs: 60_000 })).result;
    await loaded(a);
    const gets = () => store.calls.filter((c) => c.method === "GET").length;
    const before = gets();
    visibility = "hidden";
    document.dispatchEvent(new Event("visibilitychange"));
    expect(gets(), "hidden: nothing to do").toBe(before);
    act(() => a.current.commit((cur) => withBudget(cur, "Groceries", 1, T(1))));
    visibility = "visible";
    document.dispatchEvent(new Event("visibilitychange"));
    expect(gets(), "a pending edit is newer than anything the store holds").toBe(before);
    expect(a.current.doc.budgets).toEqual({ Groceries: 1 });
  });
});

// The target mix is one value, not a list: a tab sends it only when it changed it, whole, and otherwise leaves the
// store's copy alone (the store keeps an omitted target_mix, as it keeps the lists).
describe("prefs: the target mix travels only when this tab changed it", () => {
  const MIX_B = { stocks: 60, bonds: 30, cash: 5, alts: 5 };
  const MIX_A = { stocks: 80, bonds: 10, cash: 5, alts: 5 };
  it("B saves a mix; A, loaded before it, edits a budget: the PUT leaves the mix out, the store keeps B's, A adopts it", async () => {
    const store = prefsStore();
    const a = await loaded(device("dev-a"));
    const b = await loaded(device("dev-b"));
    act(() => b.current.commit((cur) => withTargetMix(cur, MIX_B, T(1))!));
    await waitFor(() => expect(store.stored.target_mix).toEqual(MIX_B));
    expect(puts(store)[0].body, "B changed it: B's PUT carries it").toHaveProperty("target_mix", MIX_B);
    expect(a.current.doc.target_mix, "A has not heard of it").toBeNull();

    act(() => a.current.commit((cur) => withBudget(cur, "Groceries", 900, T(2))));
    await waitFor(() => expect(puts(store)).toHaveLength(2));
    expect(puts(store)[1].body, "A did not change the mix: its budget PUT leaves it out").not.toHaveProperty("target_mix");
    expect(store.stored.target_mix, "B's mix survived A's save").toEqual(MIX_B);
    expect(store.stored.budgets).toEqual({ Groceries: 900 });
    await waitFor(() => expect(a.current.doc.target_mix, "the echo hands A the mix").toEqual(MIX_B));
  });

  it("A changes the mix after B saved one: A's PUT carries it whole and A's mix wins (last-write-wins)", async () => {
    const store = prefsStore();
    const a = await loaded(device("dev-a"));
    const b = await loaded(device("dev-b"));
    act(() => b.current.commit((cur) => withTargetMix(cur, MIX_B, T(1))!));
    await waitFor(() => expect(store.stored.target_mix).toEqual(MIX_B));
    act(() => a.current.commit((cur) => withTargetMix(cur, MIX_A, T(2))!));
    await waitFor(() => expect(puts(store)).toHaveLength(2));
    expect(puts(store)[1].body).toHaveProperty("target_mix", MIX_A);
    expect(store.stored.target_mix).toEqual(MIX_A);
  });

  it("a clear is sent as an explicit null, and the store drops the mix", async () => {
    const store = prefsStore({ ...emptyPrefs(), updated_at: T(0), target_mix: MIX_B });
    const a = await loaded(device("dev-a"));
    expect(a.current.doc.target_mix).toEqual(MIX_B);
    act(() => a.current.commit((cur) => ({ ...cur, updated_at: T(1), target_mix: null })));
    await waitFor(() => expect(puts(store)).toHaveLength(1));
    expect(puts(store)[0].body).toHaveProperty("target_mix", null);
    expect(store.stored.target_mix).toBeNull();
  });

  it("an echo that arrives while A's next edit waits rebases the mix too: the waiting budget PUT does not send A's stale copy", async () => {
    const s = ctlStore({ ...emptyPrefs(), updated_at: T(0) });
    const a = await loaded(prefsDevice("dev-a", { debounceMs: 150 }).result);
    const b = await loaded(prefsDevice("dev-b").result);
    act(() => b.current.commit((cur) => withTargetMix(cur, MIX_B, T(1))!));
    await waitFor(() => expect(s.stored.target_mix).toEqual(MIX_B));
    s.holdPutResp = 1;
    act(() => a.current.commit((cur) => withSaved(cur, "saved_filters", view("W"), T(2))!));   // goes at once
    await waitFor(() => expect(s.heldPutResp).toHaveLength(1));
    act(() => a.current.commit((cur) => withBudget(cur, "Groceries", 5, T(3))));            // waits
    s.heldPutResp[0]();                                                                        // the echo holds B's mix
    await waitFor(() => expect(a.current.doc.target_mix, "A's waiting doc takes the store's mix").toEqual(MIX_B));
    await waitFor(() => expect(s.stored.budgets).toEqual({ Groceries: 5 }));
    const last = s.calls.filter((c) => c.method === "PUT").at(-1)!;
    expect(last.body, "the budget PUT leaves the mix out").not.toHaveProperty("target_mix");
    expect(s.stored.target_mix, "B's mix survives").toEqual(MIX_B);
  });

  it("a mix change waiting in the mirror when the page went away is pushed on the next load over an older store copy", async () => {
    const store = prefsStore({ ...emptyPrefs(), updated_at: T(0) });
    const acked = { saved_filters: [], reports: [], target_mix: null };
    localStorage.setItem("dev-a", JSON.stringify({ ...emptyPrefs(), updated_at: T(5), target_mix: MIX_A, _acked: acked }));
    await loaded(device("dev-a"));
    await waitFor(() => expect(puts(store)).toHaveLength(1));
    expect(puts(store)[0].body).toHaveProperty("target_mix", MIX_A);
    expect(store.stored.target_mix).toEqual(MIX_A);
  });
});

// A tab on a bundle older than the modules and the AI consent reads a doc without them and saves one without them; the
// store keeps both (merge_prefs), and a tab on this bundle sends either only when it changed it.
describe("prefs: no save erases the modules or the AI consent a tab did not change", () => {
  const AI = { enabled: true, consent_policy: 2, consent_scope: "anthropic||cards", consented_at: T(0) };
  const stored = () => ({ ...emptyPrefs(), updated_at: T(0), modules: { home_buying: true }, ai: AI });
  /** the previous bundle's prefs options: its documents know neither section, and it keeps only the target mix whole */
  const olderDevice = (localKey: string) => renderHook(() => useSyncedDoc<PrefsDoc>({
    ...PREFS_SYNC, localKey, debounceMs: 5, keepWhole: ["target_mix"],
    normalize: (raw) => {
      const { modules: _modules, ai: _ai, ...known } = normalizePrefs(raw);
      return known as PrefsDoc;
    },
    empty: () => {
      const { modules: _modules, ai: _ai, ...known } = emptyPrefs();
      return known as PrefsDoc;
    },
  })).result;

  it("an older bundle's budget save carries neither section, and the store keeps both", async () => {
    const store = prefsStore(stored());
    const old = await loaded(olderDevice("dev-old"));
    expect("modules" in old.current.doc || "ai" in old.current.doc, "the older bundle reads neither").toBe(false);
    act(() => old.current.commit((cur) => withBudget(cur, "Groceries", 900, T(1))));
    await waitFor(() => expect(puts(store)).toHaveLength(1));
    expect(puts(store)[0].body).not.toHaveProperty("modules");
    expect(puts(store)[0].body).not.toHaveProperty("ai");
    expect(store.stored.modules, "the module stays on").toEqual({ home_buying: true });
    expect(store.stored.ai, "the consent stays").toEqual(AI);
    expect(store.stored.budgets).toEqual({ Groceries: 900 });
    // and a tab on this bundle, loaded after, reads both
    const now = await loaded(device("dev-now"));
    expect(now.current.doc.modules).toEqual({ home_buying: true });
    expect(now.current.doc.ai).toEqual(AI);
  });

  it("this bundle leaves both out of a budget save, and sends the modules whole once it switches one", async () => {
    const store = prefsStore(stored());
    const a = await loaded(device("dev-a"));
    act(() => a.current.commit((cur) => withBudget(cur, "Groceries", 900, T(1))));
    await waitFor(() => expect(puts(store)).toHaveLength(1));
    expect(puts(store)[0].body).not.toHaveProperty("modules");
    expect(puts(store)[0].body).not.toHaveProperty("ai");
    act(() => a.current.commit((cur) => withModule(cur, "home_buying", false, T(2))));
    await waitFor(() => expect(puts(store)).toHaveLength(2));
    expect(puts(store)[1].body).toHaveProperty("modules", { home_buying: false });
    expect(puts(store)[1].body, "the consent it did not change stays out").not.toHaveProperty("ai");
    expect(store.stored.modules).toEqual({ home_buying: false });
    expect(store.stored.ai).toEqual(AI);
  });

  it("turning AI off here sends the consent whole, and a tab loaded before it cannot turn it back on or erase it", async () => {
    const store = prefsStore(stored());
    const stale = await loaded(device("dev-stale"));             // loaded while AI was on
    const older = await loaded(olderDevice("dev-old"));
    const a = await loaded(device("dev-a"));
    act(() => a.current.commit((cur) => withAi(cur, { ...cur.ai, enabled: false }, T(1))));
    await waitFor(() => expect(puts(store)).toHaveLength(1));
    const off = { ...AI, enabled: false };
    expect(puts(store)[0].body).toHaveProperty("ai", off);
    expect(store.stored.ai).toEqual(off);
    // the stale tab still shows AI on, and saves a budget; the older bundle saves one too
    expect(stale.current.doc.ai.enabled, "the stale tab read it on").toBe(true);
    act(() => stale.current.commit((cur) => withBudget(cur, "Groceries", 900, T(2))));
    await waitFor(() => expect(puts(store)).toHaveLength(2));
    expect(puts(store)[1].body, "a section the tab did not change stays out of its save").not.toHaveProperty("ai");
    act(() => older.current.commit((cur) => withBudget(cur, "Dining", 300, T(3))));
    await waitFor(() => expect(puts(store)).toHaveLength(3));
    expect(store.stored.ai, "still off, as the family left it").toEqual(off);
    expect(store.stored.budgets).toEqual({ Dining: 300 });
  });

  it("a later bundle's switch survives this tab's: the module this bundle does not know rides along in the PUT", async () => {
    const store = prefsStore({ ...stored(), modules: { home_buying: false, later: true } });
    const a = await loaded(device("dev-a"));
    expect(a.current.doc.modules, "read and kept").toEqual({ home_buying: false, later: true });
    act(() => a.current.commit((cur) => withModule(cur, "home_buying", true, T(1))));
    await waitFor(() => expect(puts(store)).toHaveLength(1));
    expect(puts(store)[0].body).toHaveProperty("modules", { home_buying: true, later: true });
    expect(store.stored.modules).toEqual({ home_buying: true, later: true });
  });
});

// A whole section switched and switched back before the first save is answered is back at the copy the store last
// confirmed; the store holds the first save's value all the same, so the second save must carry it.
describe("prefs: a whole section changed here goes out until a save carrying its current value is answered", () => {
  it("set, then cleared before the first reply: the second PUT carries modules {} and the store ends with {}", async () => {
    const s = ctlStore({ ...emptyPrefs(), updated_at: T(0) });
    const a = await loaded(prefsDevice("dev-a").result);
    s.holdPutResp = 1;
    act(() => a.current.commit((cur) => withModule(cur, "home_buying", true, T(1))));
    await waitFor(() => expect(s.heldPutResp).toHaveLength(1));
    expect(s.stored.modules, "the first save reached the store").toEqual({ home_buying: true });
    act(() => a.current.commit((cur) => withModule(cur, "home_buying", null, T(2))));
    await waitFor(() => expect(puts(s)).toHaveLength(2));
    expect(puts(s)[1].body, "back at the confirmed copy, and sent all the same").toHaveProperty("modules", {});
    s.heldPutResp[0]();                                                     // the first reply, now stale
    await settle(50);
    expect(s.stored.modules).toEqual({});
    expect(a.current.doc.modules, "the switch stays off").toEqual({});
  });

  it("a save whose reply failed keeps the section marked: the next PUT carries it", async () => {
    let stored: Json = { ...emptyPrefs(), updated_at: T(0) };
    const calls: Call[] = [];
    let failReply = 1;
    vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) as Json : null;
      calls.push({ method, body, keepalive: !!init?.keepalive });
      if (method === "PUT" && body) {
        stored = storeMerge(body, stored);                                  // the store applies it...
        if (failReply > 0) { failReply -= 1; throw new TypeError("connection reset"); }   // ...and the reply is lost
      }
      return new Response(JSON.stringify(stored), { status: 200 });
    });
    const a = await loaded(prefsDevice("dev-a", { retryMs: 60_000 }).result);
    act(() => a.current.commit((cur) => withModule(cur, "home_buying", true, T(1))));
    await waitFor(() => expect(a.current.localOnly).toBe(true));
    act(() => a.current.commit((cur) => withModule(cur, "home_buying", null, T(2))));
    await waitFor(() => expect(puts({ calls })).toHaveLength(2));
    expect(puts({ calls })[1].body).toHaveProperty("modules", {});
    await waitFor(() => expect(a.current.localOnly).toBe(false));
    expect(stored.modules).toEqual({});
  });

  it("only the newest save's answer confirms a mark: an older reply carrying the same value by chance leaves it", async () => {
    const s = ctlStore({ ...emptyPrefs(), updated_at: T(0) });
    const a = await loaded(prefsDevice("dev-a", { retryMs: 30 }).result);
    s.holdPutResp = 3;
    act(() => a.current.commit((cur) => withModule(cur, "home_buying", true, T(1))));    // PUT 1 carries on
    await waitFor(() => expect(s.heldPutResp).toHaveLength(1));
    act(() => a.current.commit((cur) => withModule(cur, "home_buying", null, T(2))));    // PUT 2 carries {}
    await waitFor(() => expect(s.heldPutResp).toHaveLength(2));
    act(() => a.current.commit((cur) => withModule(cur, "home_buying", true, T(3))));    // PUT 3 carries on
    await waitFor(() => expect(s.heldPutResp).toHaveLength(3));
    s.down = true;
    act(() => a.current.commit((cur) => withModule(cur, "home_buying", null, T(4))));    // PUT 4 never arrives
    await waitFor(() => expect(a.current.localOnly).toBe(true));
    expect(s.stored.modules, "the store holds PUT 3's on").toEqual({ home_buying: true });
    s.heldPutResp[1]();                     // PUT 2's late answer: it carried {}, which is the doc's value again
    s.heldPutResp[0]();
    s.heldPutResp[2]();
    await settle(10);
    s.down = false;
    await waitFor(() => expect(s.stored.modules, "the retry still carries the section").toEqual({}));
  });

  it("offline, a section switched and switched back is this tab's on reconnect: it goes out, and its answer clears it", async () => {
    const s = ctlStore({ ...emptyPrefs(), updated_at: T(0) });
    s.down = true;
    const a = await loaded(prefsDevice("dev-a", { retryMs: 10 }).result);
    expect(a.current.localOnly).toBe(true);
    act(() => a.current.commit((cur) => withModule(cur, "home_buying", true, T(1))));
    act(() => a.current.commit((cur) => withModule(cur, "home_buying", null, T(2))));
    s.stored = { ...s.stored, updated_at: T(3), modules: { home_buying: false } };       // another device, meanwhile
    s.down = false;
    comeBack();
    await waitFor(() => expect(s.stored.modules, "this tab's last word goes out").toEqual({}));
    await waitFor(() => expect(a.current.localOnly).toBe(false));
    s.stored = { ...s.stored, updated_at: T(5), modules: { home_buying: true } };        // another device, later
    act(() => a.current.commit((cur) => withBudget(cur, "Groceries", 900, T(6))));
    await waitFor(() => expect(s.stored.budgets).toEqual({ Groceries: 900 }));
    expect(puts(s).at(-1)!.body, "the mark was cleared: the later save leaves the section out").not.toHaveProperty("modules");
    expect(s.stored.modules).toEqual({ home_buying: true });
  });

  it("once a save carrying it is answered, a section this tab did not change again stays out of the next save", async () => {
    const store = prefsStore({ ...emptyPrefs(), updated_at: T(0) });
    const a = await loaded(device("dev-a"));
    act(() => a.current.commit((cur) => withModule(cur, "home_buying", true, T(1))));
    await waitFor(() => expect(store.stored.modules).toEqual({ home_buying: true }));
    await waitFor(() => expect(a.current.saveState).toBe("saved"));
    act(() => a.current.commit((cur) => withBudget(cur, "Groceries", 900, T(2))));
    await waitFor(() => expect(puts(store)).toHaveLength(2));
    expect(puts(store)[1].body, "the confirmed switch is not sent again").not.toHaveProperty("modules");
    expect(store.stored.modules).toEqual({ home_buying: true });
  });
});

describe("prefs: a list edit merges by id with the store's list as it is NOW (an always-visible tab included)", () => {
  const stored = (...ids_: string[]) => ({ ...emptyPrefs(), updated_at: T(0), saved_filters: ids_.map((id) => ({ ...view(id), created_at: T(0) })) });
  async function twoDevices(initial: Json) {
    const store = prefsStore(initial);
    const a = await loaded(device("dev-a"));
    const b = await loaded(device("dev-b"));
    return { store, a, b };
  }
  it("A deletes X while B added V: X is gone and V survives", async () => {
    const { store, a, b } = await twoDevices(stored("X"));
    act(() => b.current.commit((cur) => withSaved(cur, "saved_filters", view("V"), T(1))!));
    await waitFor(() => expect(ids(store.stored.saved_filters)).toEqual(["X", "V"]));
    act(() => a.current.commit((cur) => withoutSaved(cur, "saved_filters", "X", T(2))));      // A never left the tab
    await waitFor(() => expect(puts(store)).toHaveLength(2));
    expect(ids(store.stored.saved_filters)).toEqual(["V"]);
    await waitFor(() => expect(ids(a.current.doc.saved_filters), "A now shows the family's list").toEqual(["V"]));
  });
  it("A adds W while B added V: both survive", async () => {
    const { store, a, b } = await twoDevices(stored("X"));
    act(() => b.current.commit((cur) => withSaved(cur, "saved_filters", view("V"), T(1))!));
    await waitFor(() => expect(puts(store)).toHaveLength(1));
    act(() => a.current.commit((cur) => withSaved(cur, "saved_filters", view("W"), T(2))!));
    await waitFor(() => expect(puts(store)).toHaveLength(2));
    expect(ids(store.stored.saved_filters)).toEqual(["X", "V", "W"]);
  });
  it("A renames V while B deleted V: the deletion wins (V stays gone)", async () => {
    const { store, a, b } = await twoDevices(stored("X", "V"));
    act(() => b.current.commit((cur) => withoutSaved(cur, "saved_filters", "V", T(1))));
    await waitFor(() => expect(ids(store.stored.saved_filters)).toEqual(["X"]));
    act(() => a.current.commit((cur) => withRenamedSaved(cur, "saved_filters", "V", "V renamed", T(2))!));
    await waitFor(() => expect(puts(store)).toHaveLength(2));
    expect(ids(store.stored.saved_filters)).toEqual(["X"]);
  });
  it("A renames X while B added V: A's rename and B's view both land", async () => {
    const { store, a, b } = await twoDevices(stored("X"));
    act(() => b.current.commit((cur) => withSaved(cur, "saved_filters", view("V"), T(1))!));
    await waitFor(() => expect(puts(store)).toHaveLength(1));
    act(() => a.current.commit((cur) => withRenamedSaved(cur, "saved_filters", "X", "X renamed", T(2))!));
    await waitFor(() => expect(puts(store)).toHaveLength(2));
    expect((store.stored.saved_filters as { id: string; name: string }[]).map((v) => `${v.id}:${v.name}`)).toEqual(["X:X renamed", "V:V"]);
  });
});

describe("prefs: a list edit still waiting when the page goes away is never sent unmerged", () => {
  /** A (a 60 s debounce, so the edit is still waiting) adds W while B has added V; then A's page hides */
  async function hiddenWithAnEdit() {
    const store = prefsStore();
    const a = renderHook(() => useSyncedDoc<PrefsDoc>({ ...PREFS_SYNC, localKey: "dev-a", debounceMs: 60_000 }));
    await loaded(a.result);
    const b = await loaded(device("dev-b"));
    act(() => b.current.commit((cur) => withSaved(cur, "saved_filters", view("V"), T(1))!));
    await waitFor(() => expect(ids(store.stored.saved_filters)).toEqual(["V"]));
    act(() => a.result.current.commit((cur) => withBudget(withSaved(cur, "saved_filters", view("W"), T(2))!, "Groceries", 5, T(2))));
    window.dispatchEvent(new Event("pagehide"));
    await waitFor(() => expect(puts(store).filter((c) => c.keepalive)).toHaveLength(1));
    return { store, a };
  }
  it("the flush leaves the changed list out (the store keeps V) but sends the rest; the mirror keeps W and its base", async () => {
    const { store } = await hiddenWithAnEdit();
    const flush = puts(store).find((c) => c.keepalive)!;
    expect(flush.body, "no unmerged list goes out").not.toHaveProperty("saved_filters");
    expect(flush.body!.budgets).toEqual({ Groceries: 5 });
    expect(ids(store.stored.saved_filters), "V untouched").toEqual(["V"]);
    expect(store.stored.budgets).toEqual({ Groceries: 5 });
    const mirror = JSON.parse(localStorage.getItem("dev-a")!);
    expect(ids(mirror.saved_filters)).toEqual(["W"]);
    expect(mirror._acked.saved_filters, "the base the next merge starts from").toEqual([]);
  });
  it("the next load merges W in and keeps V", async () => {
    const { store, a } = await hiddenWithAnEdit();
    a.unmount();
    const again = await loaded(device("dev-a"));
    await waitFor(() => expect(ids(store.stored.saved_filters)).toEqual(["V", "W"]));
    await waitFor(() => expect(ids(again.current.doc.saved_filters)).toEqual(["V", "W"]));
  });
  it("a page that comes back from the back/forward cache merges W in on pageshow (the re-read does not drop it)", async () => {
    const { store, a } = await hiddenWithAnEdit();
    window.dispatchEvent(new Event("pageshow"));
    await waitFor(() => expect(ids(store.stored.saved_filters)).toEqual(["V", "W"]));
    await waitFor(() => expect(ids(a.result.current.doc.saved_filters)).toEqual(["V", "W"]));
  });
});

describe("mergeById", () => {
  const it_ = (id: string, name = id) => ({ id, name });
  it("server order, less local deletions, local edits in place, local additions last; a remote deletion beats a local edit", () => {
    const base = [it_("a"), it_("b"), it_("c")];
    const local = [it_("a", "a2"), it_("c", "c2"), it_("n")];                  // b deleted, a and c edited, n added
    const server = [it_("z"), it_("b"), it_("a")];                              // c deleted there, z added there
    expect(mergeById(base, local, server)).toEqual([it_("z"), it_("a", "a2"), it_("n")]);
  });
  it("an unchanged local copy hands back the server's list", () => {
    const server = [it_("x"), it_("y", "y2")];
    expect(mergeById([it_("y")], [it_("y")], server)).toEqual(server);
  });
});

describe("prefs: the load-time push of an edit that never landed keeps the store's lists it did not edit", () => {
  const server = (): Json => ({ ...emptyPrefs(), updated_at: T(1), saved_filters: [{ ...view("V"), created_at: T(1) }] });
  /** this browser's mirror: a budget edit at T(2) that never reached the store, made from a doc with no views */
  const mirror = (extra: Json) =>
    localStorage.setItem("dev-a", JSON.stringify({ ...emptyPrefs(), updated_at: T(2), budgets: { Groceries: 7 }, ...extra }));

  it("lists the local copy did not edit (equal to the acknowledged copy in the mirror) are omitted and taken from the store", async () => {
    const store = prefsStore(server());
    mirror({ _acked: { saved_filters: [], reports: [] } });
    const a = await loaded(device("dev-a"));
    await waitFor(() => expect(puts(store)).toHaveLength(1));
    expect(puts(store)[0].body).not.toHaveProperty("saved_filters");
    expect(ids(store.stored.saved_filters), "V survived the push").toEqual(["V"]);
    expect(store.stored.budgets, "the edit that never landed did").toEqual({ Groceries: 7 });
    expect(ids(a.current.doc.saved_filters)).toEqual(["V"]);
    expect(a.current.doc.budgets).toEqual({ Groceries: 7 });
  });

  it("a mirror with no acknowledged copy (an older bundle wrote it) counts as not having edited the lists", async () => {
    const store = prefsStore(server());
    mirror({});
    await loaded(device("dev-a"));
    await waitFor(() => expect(puts(store)).toHaveLength(1));
    expect(ids(store.stored.saved_filters)).toEqual(["V"]);
  });

  it("a list the local copy did edit is merged by id with the store's (the edit that never landed is not lost, nor is V)", async () => {
    const store = prefsStore(server());
    mirror({ saved_filters: [{ ...view("W"), created_at: T(2) }], _acked: { saved_filters: [], reports: [] } });
    await loaded(device("dev-a"));
    await waitFor(() => expect(puts(store)).toHaveLength(1));
    expect(ids(puts(store)[0].body!.saved_filters)).toEqual(["V", "W"]);
    expect(puts(store)[0].body, "the unedited list still stays out").not.toHaveProperty("reports");
  });

  it("a garbage acknowledged copy in the mirror is no base at all (treated like a mirror without one), and a malformed entry in it is dropped", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const store = prefsStore(server());
      mirror({ _acked: { saved_filters: "garbage", reports: {} } });
      const a = await loaded(device("dev-a"));
      await waitFor(() => expect(puts(store)).toHaveLength(1));
      expect(ids(store.stored.saved_filters), "no crash; the store's lists win, as with no base").toEqual(["V"]);
      expect(a.current.doc.budgets).toEqual({ Groceries: 7 });
      cleanup();
      // a base entry that is not a valid saved view is normalized away: W, valid here and gone from the base,
      // counts as this browser's addition and is kept
      localStorage.clear();
      const store2 = prefsStore(server());
      mirror({ saved_filters: [{ ...view("W"), created_at: T(2) }], _acked: { saved_filters: [{ id: "W", name: null }], reports: [] } });
      await loaded(device("dev-a"));
      await waitFor(() => expect(puts(store2)).toHaveLength(1));
      expect(ids(store2.stored.saved_filters)).toEqual(["V", "W"]);
    } finally { warn.mockRestore(); }
  });

  it("the mirror records what the store acknowledged, beside the doc (the next load's base)", async () => {
    prefsStore(server());
    const a = await loaded(device("dev-a"));
    act(() => a.current.commit((cur) => withBudget(cur, "Groceries", 3, T(3))));
    const raw = JSON.parse(localStorage.getItem("dev-a")!);
    expect(ids(raw._acked.saved_filters)).toEqual(["V"]);
    expect(raw._acked.reports).toEqual([]);
    expect(raw.budgets).toEqual({ Groceries: 3 });
  });
});

// ------------------------------------------------------------------ the transport itself

interface Doc { updated_at: string; items: string[] }
const normalizeDoc = (raw: unknown): Doc => {
  const d = (raw ?? {}) as Partial<Doc>;
  return { updated_at: typeof d.updated_at === "string" ? d.updated_at : EPOCH_ISO,
           items: Array.isArray(d.items) ? d.items.filter((x): x is string => typeof x === "string") : [] };
};
const emptyDoc = (): Doc => ({ updated_at: EPOCH_ISO, items: [] });

interface Held { body: Json | null; keepalive: boolean; answer: (status: number, body?: unknown) => void; fail: () => void }
/** GET answers `get` at once; every PUT waits in `held` until the test answers (or fails) it */
function heldStore(get: Json | (() => Response)) {
  const held: Held[] = [];
  vi.stubGlobal("fetch", (_url: string, init?: RequestInit) => new Promise<Response>((resolve, reject) => {
    if ((init?.method ?? "GET") === "GET") {
      resolve(typeof get === "function" ? get() : new Response(JSON.stringify(get), { status: 200 }));
      return;
    }
    held.push({ body: JSON.parse(String(init!.body)), keepalive: !!init?.keepalive,
                answer: (status, body) => resolve(new Response(JSON.stringify(body ?? {}), { status })),
                fail: () => reject(new TypeError("network down")) });
  }));
  return held;
}
const hook = (o: { adoptEcho?: boolean; debounceMs?: number; retryMs?: number; resyncOnVisible?: boolean } = {}) =>
  renderHook(() => useSyncedDoc<Doc>({ url: "./api/doc", localKey: "doc", normalize: normalizeDoc, empty: emptyDoc,
                                        debounceMs: 1, ...o })).result;
const settle = (ms = 30) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });

describe("useSyncedDoc: echoes", () => {
  it("a late echo of an older PUT is not adopted over the newer one's", async () => {
    const held = heldStore({ updated_at: T(0), items: [] });
    const h = hook({ adoptEcho: true });
    await loaded(h);
    act(() => h.current.commit({ updated_at: T(1), items: ["A"] }));
    await waitFor(() => expect(held).toHaveLength(1));
    act(() => h.current.commit({ updated_at: T(2), items: ["A", "B"] }));
    await waitFor(() => expect(held).toHaveLength(2));
    held[1].answer(200, { updated_at: T(2), items: ["A", "B", "Z"] });     // the store merged in another member's Z
    await waitFor(() => expect(h.current.doc.items).toEqual(["A", "B", "Z"]));
    held[0].answer(200, { updated_at: T(1), items: ["A"] });              // ...then PUT A's echo turns up
    await settle();
    expect(h.current.doc.items, "B's echo is the newer truth").toEqual(["A", "B", "Z"]);
  });

  it("an echo equal to the doc (key order aside) is not adopted: the doc keeps its identity, nothing recomputes", async () => {
    const held = heldStore({ updated_at: T(0), items: [] });
    const h = hook({ adoptEcho: true });
    await loaded(h);
    const mine: Doc = { updated_at: T(1), items: ["A"] };
    act(() => h.current.commit(mine));
    await waitFor(() => expect(held).toHaveLength(1));
    held[0].answer(200, { items: ["A"], updated_at: T(1) });
    await waitFor(() => expect(h.current.saveState).toBe("saved"));
    expect(h.current.doc).toBe(mine);
  });
});

describe("the answers store's echo", () => {
  it("an echo that only reorders the answers (the store keeps them oldest first, by key) is not adopted", async () => {
    const held = heldStore({ version: 1, updated_at: EPOCH_ISO, answers: [] });
    const h = renderHook(() => useAnswersStore()).result;
    await loaded(h);
    act(() => h.current.answerTxns(["b", "a"], { tags: ["kids"] }));       // one stamp, kept here in selection order
    const mine = h.current.doc;
    expect(mine.answers.map((a) => a.key)).toEqual(["txn:b", "txn:a"]);
    await waitFor(() => expect(held).toHaveLength(1), { timeout: 3000 });  // the store's own 800 ms debounce
    const sent = held[0].body as { answers: { key: string }[] };
    held[0].answer(200, { ...sent, answers: [...sent.answers].sort((x, y) => (x.key < y.key ? -1 : 1)) });
    await settle(50);
    expect(h.current.doc, "same answers, same doc: nothing downstream recomputes").toBe(mine);
  });
});

describe("useSyncedDoc: a failed PUT", () => {
  it("is retried with backoff, and the success clears the local-only state", async () => {
    const held = heldStore({ updated_at: T(0), items: [] });
    const h = hook({ retryMs: 5 });
    await loaded(h);
    act(() => h.current.commit({ updated_at: T(1), items: ["A"] }));
    await waitFor(() => expect(held).toHaveLength(1));
    held[0].fail();                                                        // the store is restarting (a deploy)
    await waitFor(() => expect(h.current.localOnly).toBe(true));
    expect(h.current.saveState).toBe("error");
    expect(h.current.refused, "unreachable is not refused").toBeNull();
    await waitFor(() => expect(held).toHaveLength(2));                     // the retry
    expect(held[1].body).toEqual({ updated_at: T(1), items: ["A"] });
    held[1].answer(503);                                                   // still restarting
    await waitFor(() => expect(held).toHaveLength(3));
    held[2].answer(200, {});
    await waitFor(() => expect(h.current.localOnly).toBe(false));
    expect(h.current.saveState).toBe("saved");
  });

  it("does not stop the next edit from reaching the store, and that edit's success clears the latch", async () => {
    const held = heldStore({ updated_at: T(0), items: [] });
    const h = hook({ retryMs: 60_000 });                                   // the retry itself stays out of the way
    await loaded(h);
    act(() => h.current.commit({ updated_at: T(1), items: ["A"] }));
    await waitFor(() => expect(held).toHaveLength(1));
    held[0].answer(502);
    await waitFor(() => expect(h.current.localOnly).toBe(true));
    act(() => h.current.commit({ updated_at: T(2), items: ["A", "B"] }));
    await waitFor(() => expect(held).toHaveLength(2));
    expect(held[1].body).toEqual({ updated_at: T(2), items: ["A", "B"] });
    held[1].answer(200, {});
    await waitFor(() => expect(h.current.localOnly).toBe(false));
  });

  it("a document the store refused (400, 413) is not sent again: only a new edit is", async () => {
    for (const status of [400, 413]) {
      localStorage.clear();
      const held = heldStore({ updated_at: T(0), items: [] });
      const h = hook({ retryMs: 1 });
      await loaded(h);
      act(() => h.current.commit({ updated_at: T(1), items: ["big"] }));
      await waitFor(() => expect(held).toHaveLength(1));
      held[0].answer(status, { error: "no" });
      await waitFor(() => expect(h.current.localOnly).toBe(true));
      expect(h.current.refused, "the UI can say the store refused it, not that it is unreachable").toBe(status);
      await settle();
      expect(held, `${status}: no retry`).toHaveLength(1);
      act(() => h.current.commit({ updated_at: T(2), items: [] }));
      await waitFor(() => expect(held, `${status}: the next edit is sent`).toHaveLength(2));
      held[1].answer(200, {});
      await waitFor(() => expect(h.current.localOnly).toBe(false));
      expect(h.current.refused).toBeNull();
      cleanup();
    }
  });
});

describe("useSyncedDoc: what a caller learns of its save", () => {
  it("a refusal carries the store's own words until a save gets through; the counters move on the newest PUT's outcome", async () => {
    const held = heldStore({ updated_at: T(0), items: [] });
    const h = await loaded(hook());
    expect([h.current.accepted, h.current.refusals, h.current.refusedReason]).toEqual([0, 0, null]);
    act(() => h.current.commit({ updated_at: T(1), items: ["A"] }));
    await waitFor(() => expect(held).toHaveLength(1));
    held[0].answer(400, { error: "at most 50 assets" });
    await waitFor(() => expect(h.current.refused).toBe(400));
    expect([h.current.accepted, h.current.refusals, h.current.refusedReason]).toEqual([0, 1, "at most 50 assets"]);
    act(() => h.current.commit({ updated_at: T(2), items: [] }));
    await waitFor(() => expect(held).toHaveLength(2));
    held[1].answer(200, {});
    await waitFor(() => expect(h.current.accepted).toBe(1));
    expect([h.current.refusals, h.current.refusedReason, h.current.refused]).toEqual([1, null, null]);
  });
  it("a refusal with no words, and an unreachable store, give no reason; an older PUT's late success counts nothing", async () => {
    const held = heldStore({ updated_at: T(0), items: [] });
    const h = await loaded(hook({ retryMs: 60_000 }));
    act(() => h.current.commit({ updated_at: T(1), items: ["A"] }));
    await waitFor(() => expect(held).toHaveLength(1));
    held[0].answer(413, "not json at all");
    await waitFor(() => expect(h.current.refused).toBe(413));
    expect(h.current.refusedReason).toBeNull();
    act(() => h.current.commit({ updated_at: T(2), items: ["B"] }));
    await waitFor(() => expect(held).toHaveLength(2));
    act(() => h.current.commit({ updated_at: T(3), items: ["C"] }));
    await waitFor(() => expect(held).toHaveLength(3));
    held[1].answer(200, {});                                                // the older one lands late
    await settle();
    expect(h.current.accepted, "not the newest PUT").toBe(0);
    held[2].fail();
    await waitFor(() => expect(h.current.localOnly).toBe(true));
    expect([h.current.accepted, h.current.refusals]).toEqual([0, 1]);
  });
});

describe("useSyncedDoc: a merge option for an item-by-item store", () => {
  /** union by item, the local copy's items first: a stand-in for a store's own merge */
  const union = (local: Doc, server: Doc): Doc => ({ updated_at: local.updated_at > server.updated_at ? local.updated_at : server.updated_at,
    items: [...new Set([...local.items, ...server.items])] });
  it("the load merges the mirror with the store's newer copy instead of dropping it, and pushes what the store lacks", async () => {
    const held = heldStore({ updated_at: T(5), items: ["theirs"] });
    localStorage.setItem("doc", JSON.stringify({ updated_at: T(1), items: ["mine, never sent"] }));
    const h = renderHook(() => useSyncedDoc<Doc>({ url: "./api/doc", localKey: "doc", normalize: normalizeDoc,
      empty: emptyDoc, debounceMs: 1, merge: union })).result;
    await loaded(h);
    expect(h.current.doc.items).toEqual(["mine, never sent", "theirs"]);
    await waitFor(() => expect(held).toHaveLength(1));
    expect(held[0].body).toEqual({ updated_at: T(5), items: ["mine, never sent", "theirs"] });
  });
  it("a mirror holding nothing new pushes nothing", async () => {
    const held = heldStore({ updated_at: T(5), items: ["theirs"] });
    localStorage.setItem("doc", JSON.stringify({ updated_at: T(1), items: ["theirs"] }));
    const h = renderHook(() => useSyncedDoc<Doc>({ url: "./api/doc", localKey: "doc", normalize: normalizeDoc,
      empty: emptyDoc, debounceMs: 1, merge: union })).result;
    await loaded(h);
    await settle();
    expect(held).toHaveLength(0);
  });
});

describe("useSyncedDoc: a refused edit stays in view", () => {
  it("coming back to the tab after a 413 does not replace the refused edit with the store's copy", async () => {
    const held = heldStore({ updated_at: T(0), items: ["the store"] });
    const h = await loaded(hook({ resyncOnVisible: true }));
    act(() => h.current.commit({ updated_at: T(1), items: ["the store", "too much"] }));
    await waitFor(() => expect(held).toHaveLength(1));
    held[0].answer(413, { error: "merged document would be too large" });
    await waitFor(() => expect(h.current.refused).toBe(413));
    comeBack();
    await settle();
    expect(h.current.doc.items, "the edit the store refused is still what this browser shows").toEqual(["the store", "too much"]);
    expect(h.current.localOnly).toBe(true);
  });
});

describe("useSyncedDoc: load", () => {
  it("an updated_at tie goes to the server's copy, and nothing is pushed", async () => {
    localStorage.setItem("doc", JSON.stringify({ updated_at: T(5), items: ["this browser"] }));
    const held = heldStore({ updated_at: T(5), items: ["the store"] });
    const h = await loaded(hook());
    expect(h.current.doc.items).toEqual(["the store"]);
    await settle();
    expect(held).toHaveLength(0);
  });

  it("a newer local copy (a save that never landed) wins and is pushed", async () => {
    localStorage.setItem("doc", JSON.stringify({ updated_at: T(6), items: ["unsent"] }));
    const held = heldStore({ updated_at: T(5), items: ["the store"] });
    const h = await loaded(hook());
    expect(h.current.doc.items).toEqual(["unsent"]);
    await waitFor(() => expect(held).toHaveLength(1));
    expect(held[0].body).toEqual({ updated_at: T(6), items: ["unsent"] });
  });

  it("a load that never reached the store keeps edits in this browser (no PUT); coming back to the tab reconnects and pushes them", async () => {
    let up = false;
    const held = heldStore(() => (up ? new Response(JSON.stringify({ updated_at: T(1), items: ["the store"] }), { status: 200 })
                                     : new Response("bad gateway", { status: 502 })));
    const h = await loaded(hook({ resyncOnVisible: true }));
    expect(h.current.localOnly).toBe(true);
    act(() => h.current.commit({ updated_at: T(2), items: ["offline edit"] }));
    await settle();
    expect(held, "no store at load: nothing is PUT").toHaveLength(0);
    expect(JSON.parse(localStorage.getItem("doc")!).items).toEqual(["offline edit"]);
    up = true;
    comeBack();
    await waitFor(() => expect(held).toHaveLength(1));
    expect(held[0].body).toEqual({ updated_at: T(2), items: ["offline edit"] });
    expect(h.current.localOnly).toBe(false);
  });
});

describe("useSyncedDoc: a pending save survives the page going away", () => {
  it("pagehide flushes it at once, as a keepalive PUT", async () => {
    const held = heldStore({ updated_at: T(0), items: [] });
    const h = await loaded(hook({ debounceMs: 60_000 }));
    act(() => h.current.commit({ updated_at: T(1), items: ["A"] }));
    expect(held).toHaveLength(0);
    window.dispatchEvent(new Event("pagehide"));
    await waitFor(() => expect(held).toHaveLength(1));
    expect(held[0]).toMatchObject({ keepalive: true, body: { updated_at: T(1), items: ["A"] } });
  });

  it("so does an unmount", async () => {
    const held = heldStore({ updated_at: T(0), items: [] });
    const h = await loaded(hook({ debounceMs: 60_000 }));
    act(() => h.current.commit({ updated_at: T(1), items: ["A"] }));
    cleanup();
    await waitFor(() => expect(held).toHaveLength(1));
    expect(held[0].keepalive).toBe(true);
  });
});

describe("useSyncedDoc: the re-read on return", () => {
  const getting = () => {
    let body: Json = { updated_at: T(0), items: ["old"] };
    let fail = 0;
    const gets: number[] = [];
    vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
      if ((init?.method ?? "GET") !== "GET") return new Response("{}", { status: 200 });
      gets.push(Date.now());
      if (fail > 0) { fail -= 1; return new Response("bad gateway", { status: 502 }); }
      return new Response(JSON.stringify(body), { status: 200 });
    });
    return { set: (b: Json) => { body = b; }, failNext: (n: number) => { fail = n; }, gets };
  };
  it.each(["focus", "pageshow", "online"])("a window %s event re-reads, like visibilitychange", async (ev) => {
    const s = getting();
    const h = await loaded(hook({ resyncOnVisible: true }));
    s.set({ updated_at: T(1), items: ["another device"] });
    window.dispatchEvent(new Event(ev));
    await waitFor(() => expect(h.current.doc.items).toEqual(["another device"]));
  });
  it("a failed re-read is retried with backoff, without another event", async () => {
    const s = getting();
    const h = await loaded(hook({ resyncOnVisible: true, retryMs: 5 }));
    s.set({ updated_at: T(1), items: ["another device"] });
    s.failNext(2);
    comeBack();
    await waitFor(() => expect(h.current.doc.items).toEqual(["another device"]));
    expect(s.gets.length, "load + the failed re-read + its retries").toBeGreaterThanOrEqual(4);
  });
});

describe("useSyncedDoc: reconnecting a session whose load never reached the store", () => {
  it("prefs: a view saved while the store was restarting (an older bundle's mirror: no acknowledged copy) is kept as an addition", async () => {
    let up = false;
    const store = { stored: { ...emptyPrefs(), updated_at: T(5), saved_filters: [{ ...view("X"), created_at: T(0) }, { ...view("V"), created_at: T(5) }] } as Json,
                    puts: [] as Json[] };
    vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
      if (!up) return new Response("bad gateway", { status: 502 });
      if (init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as Json;
        store.puts.push(body);
        store.stored = { ...store.stored, ...body };
      }
      return new Response(JSON.stringify(store.stored), { status: 200 });
    });
    // this browser last saw X and Y; another device has since deleted Y and added V
    localStorage.setItem("dev-a", JSON.stringify({ ...emptyPrefs(), updated_at: T(0),
      saved_filters: [{ ...view("X"), created_at: T(0) }, { ...view("Y"), created_at: T(0) }] }));
    const a = await loaded(device("dev-a"));
    expect(a.current.localOnly).toBe(true);
    act(() => a.current.commit((cur) => withSaved(cur, "saved_filters", view("W"), T(2))!));      // older than the store's doc
    up = true;
    comeBack();
    await waitFor(() => expect(store.puts).toHaveLength(1));
    expect(ids(store.stored.saved_filters), "V from another device, W from here, Y stays deleted").toEqual(["X", "V", "W"]);
    await waitFor(() => expect(a.current.localOnly).toBe(false));
  });

  it("answers (a union store): the answers saved offline are pushed once the store answers, even when its doc is newer", async () => {
    let up = false;
    const held: Json[] = [];
    vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
      if (!up) throw new TypeError("network down");
      if (init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as Json;
        held.push(body);
        return new Response(JSON.stringify(body), { status: 200 });
      }
      return new Response(JSON.stringify({ version: 1, updated_at: "2030-01-01T00:00:00.000Z", answers: [
        { key: "txn:other", scope: "txn", txn_id: "other", set: { tags: ["x"] }, answered_at: "2030-01-01T00:00:00.000Z" }] }), { status: 200 });
    });
    const h = await loaded(renderHook(() => useAnswersStore()).result);
    expect(h.current.localOnly).toBe(true);
    act(() => h.current.answerTxn("mine", { category: "Kids", subcategory: null }));
    await settle();
    expect(held, "no store: nothing sent").toHaveLength(0);
    up = true;
    window.dispatchEvent(new Event("online"));
    await waitFor(() => expect(held).toHaveLength(1));
    expect((held[0].answers as { key: string }[]).map((a) => a.key)).toEqual(["txn:mine"]);
    await waitFor(() => expect(h.current.localOnly).toBe(false));
  });
});

describe("useSyncedDoc: an older PUT failing late", () => {
  it("after a newer PUT succeeded, it neither shows local-only nor schedules a retry", async () => {
    const held = heldStore({ updated_at: T(0), items: [] });
    const h = await loaded(hook({ retryMs: 1 }));
    act(() => h.current.commit({ updated_at: T(1), items: ["A"] }));
    await waitFor(() => expect(held).toHaveLength(1));
    act(() => h.current.commit({ updated_at: T(2), items: ["A", "B"] }));
    await waitFor(() => expect(held).toHaveLength(2));
    held[1].answer(200, {});
    await waitFor(() => expect(h.current.saveState).toBe("saved"));
    held[0].fail();
    await settle();
    expect(h.current.localOnly).toBe(false);
    expect(h.current.saveState).toBe("saved");
    expect(held, "no retry of the older doc").toHaveLength(2);
  });
});

describe("useSyncedDoc: echo adoption waits for a pending edit", () => {
  it("an echo that arrives while a newer edit is still waiting to be sent is not adopted", async () => {
    const held = heldStore({ updated_at: T(0), items: [] });
    const h = await loaded(hook({ adoptEcho: true, debounceMs: 150 }));
    act(() => h.current.commit({ updated_at: T(1), items: ["A"] }));
    await waitFor(() => expect(held).toHaveLength(1));
    act(() => h.current.commit({ updated_at: T(2), items: ["A", "B"] }));     // waiting out its debounce
    held[0].answer(200, { updated_at: T(1), items: ["A", "Z"] });
    await settle(20);
    expect(held, "B is still waiting").toHaveLength(1);
    expect(h.current.doc.items, "the newer local edit stays on screen").toEqual(["A", "B"]);
  });
});

describe("useSyncedDoc: the retry backoff", () => {
  it("retryDelay doubles per failure and caps at RETRY_MAX_MS", () => {
    expect([0, 1, 2, 3].map((n) => retryDelay(2000, n))).toEqual([2000, 4000, 8000, 16000]);
    expect(retryDelay(2000, 10)).toBe(RETRY_MAX_MS);
  });
  it("consecutive failed PUTs wait retryMs, then twice it, then four times it", async () => {
    const spy = vi.spyOn(globalThis, "setTimeout");
    try {
      const held = heldStore({ updated_at: T(0), items: [] });
      const h = await loaded(hook({ retryMs: 7 }));
      act(() => h.current.commit({ updated_at: T(1), items: ["A"] }));
      for (let i = 0; i < 3; i++) {
        await waitFor(() => expect(held).toHaveLength(i + 1));
        held[i].answer(503);
      }
      await waitFor(() => expect(held).toHaveLength(4));
      const delays = spy.mock.calls.map((c) => c[1]).filter((d): d is number => typeof d === "number" && d >= 7 && d < 1000 && d % 7 === 0);
      expect(delays).toEqual([7, 14, 28]);
    } finally { spy.mockRestore(); }
  });
});

// ------------------------------------------------------------------ narrow races

/** a prefs store (merge_prefs) whose GET answers can be held (the state is taken at request time), whose PUTs can
 *  be held before they are applied or after (their echo), or which can be down */
function ctlStore(initial: Json) {
  const s = { stored: initial, calls: [] as Call[], holdGet: 0, heldGets: [] as (() => void)[],
              holdPutResp: 0, heldPutResp: [] as (() => void)[], holdKeepalive: false, heldKeepalive: [] as (() => void)[],
              down: false };
  const apply = (body: Json) => { s.stored = storeMerge(body, s.stored); };
  vi.stubGlobal("fetch", (_url: string, init?: RequestInit) => new Promise<Response>((resolve, reject) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) as Json : null;
    const keepalive = !!init?.keepalive;
    s.calls.push({ method, body, keepalive });
    if (s.down) { reject(new TypeError("network down")); return; }
    if (method === "GET") {
      const snap = JSON.stringify(s.stored);
      const go = () => resolve(new Response(snap, { status: 200 }));
      if (s.holdGet > 0) { s.holdGet -= 1; s.heldGets.push(go); } else go();
      return;
    }
    const land = () => {
      apply(body!);
      const snap = JSON.stringify(s.stored);
      const go = () => resolve(new Response(snap, { status: 200 }));
      if (s.holdPutResp > 0) { s.holdPutResp -= 1; s.heldPutResp.push(go); } else go();
    };
    if (keepalive && s.holdKeepalive) s.heldKeepalive.push(land); else land();
  }));
  return s;
}
const gets = (s: { calls: Call[] }) => s.calls.filter((c) => c.method === "GET");
const prefsDevice = (localKey: string, o: { debounceMs?: number; retryMs?: number } = {}) =>
  renderHook(() => useSyncedDoc<PrefsDoc>({ ...PREFS_SYNC, localKey, debounceMs: 5, ...o }));
const listOf = (...xs: string[]) => ({ ...emptyPrefs(), updated_at: T(0), saved_filters: xs.map((id) => ({ ...view(id), created_at: T(0) })) });

describe("a list edit leaves at once", () => {
  it("a list edit is sent without the debounce; a budget edit still waits for it", async () => {
    const s = ctlStore(listOf("X"));
    const a = await loaded(prefsDevice("dev-a", { debounceMs: 60_000 }).result);
    act(() => a.current.commit((cur) => withBudget(cur, "Groceries", 5, T(1))));
    await settle(50);
    expect(puts(s), "the budget waits out its 60 s debounce").toHaveLength(0);
    act(() => a.current.commit((cur) => withSaved(cur, "saved_filters", view("W"), T(2))!));
    await waitFor(() => expect(ids(s.stored.saved_filters)).toEqual(["X", "W"]));
    expect(s.stored.budgets, "the waiting budget went with it").toEqual({ Groceries: 5 });
  });
});

describe("another tab of this browser left a list edit in the shared mirror", () => {
  it("a re-read merges it in and sends it, instead of writing the store's copy over it", async () => {
    const s = ctlStore(listOf("X"));
    const b = await loaded(prefsDevice("same").result);                       // tab B, idle
    const a = prefsDevice("same", { retryMs: 60_000 });                         // tab A, same browser, same mirror
    await loaded(a.result);
    s.down = true;                                                             // A's save never lands
    act(() => a.result.current.commit((cur) => withSaved(cur, "saved_filters", view("W"), T(1))!));
    await waitFor(() => expect(a.result.current.localOnly).toBe(true));
    a.unmount();                                                               // A is closed
    s.down = false;
    expect(ids(JSON.parse(localStorage.getItem("same")!).saved_filters), "the mirror holds A's W").toEqual(["X", "W"]);
    window.dispatchEvent(new Event("focus"));                                  // B comes to the front: re-read
    await waitFor(() => expect(ids(s.stored.saved_filters)).toEqual(["X", "W"]));
    await waitFor(() => expect(ids(b.current.doc.saved_filters)).toEqual(["X", "W"]));
  });
});

describe("an older PUT whose pre-PUT read answers late", () => {
  it("stands down: the newer PUT carries everything, and the store keeps W2", async () => {
    const s = ctlStore(listOf("X"));
    const a = await loaded(prefsDevice("dev-a").result);
    s.holdGet = 1;                                                             // PUT1's read: taken now, answered later
    act(() => a.current.commit((cur) => withSaved(cur, "saved_filters", view("W1"), T(1))!));
    await waitFor(() => expect(s.heldGets).toHaveLength(1));
    act(() => a.current.commit((cur) => withSaved(cur, "saved_filters", view("W2"), T(2))!));
    await waitFor(() => expect(ids(s.stored.saved_filters)).toEqual(["X", "W1", "W2"]));
    s.heldGets[0]();                                                           // PUT1's stale read answers at last
    await settle(50);
    expect(puts(s), "PUT1 never went out").toHaveLength(1);
    expect(ids(s.stored.saved_filters)).toEqual(["X", "W1", "W2"]);
    expect(ids(a.current.doc.saved_filters)).toEqual(["X", "W1", "W2"]);
  });
});

describe("an echo skipped because an edit is waiting still moves the merge base", () => {
  it("A adds W (its echo arrives while a budget edit waits); B deletes W; A's budget PUT leaves W deleted", async () => {
    const s = ctlStore(listOf("X"));
    const a = await loaded(prefsDevice("dev-a", { debounceMs: 150 }).result);
    s.holdPutResp = 1;
    act(() => a.current.commit((cur) => withSaved(cur, "saved_filters", view("W"), T(1))!));
    await waitFor(() => expect(s.heldPutResp).toHaveLength(1));               // W landed: X W
    act(() => a.current.commit((cur) => withBudget(cur, "Groceries", 5, T(2))));
    s.heldPutResp[0]();                                                        // the echo, with the budget waiting
    await settle(5);
    const b = await loaded(prefsDevice("dev-b").result);
    act(() => b.current.commit((cur) => withoutSaved(cur, "saved_filters", "W", T(3))));
    await waitFor(() => expect(ids(s.stored.saved_filters)).toEqual(["X"]));
    await waitFor(() => expect(s.stored.budgets).toEqual({ Groceries: 5 }));  // A's budget PUT
    await settle();
    expect(ids(s.stored.saved_filters), "W stays deleted").toEqual(["X"]);
  });
  it("the waiting doc is rebased onto the echo, so another device's V in it is not read as deleted here", async () => {
    const s = ctlStore(listOf("X"));
    const a = await loaded(prefsDevice("dev-a", { debounceMs: 150 }).result);
    const b = await loaded(prefsDevice("dev-b").result);
    act(() => b.current.commit((cur) => withSaved(cur, "saved_filters", view("V"), T(1))!));
    await waitFor(() => expect(ids(s.stored.saved_filters)).toEqual(["X", "V"]));
    s.holdPutResp = 1;
    act(() => a.current.commit((cur) => withSaved(cur, "saved_filters", view("W"), T(2))!));   // merged: X V W
    await waitFor(() => expect(s.heldPutResp).toHaveLength(1));
    act(() => a.current.commit((cur) => withBudget(cur, "Groceries", 5, T(3))));
    s.heldPutResp[0]();
    await waitFor(() => expect(ids(a.current.doc.saved_filters), "A now shows V").toEqual(["X", "V", "W"]));
    await waitFor(() => expect(s.stored.budgets).toEqual({ Groceries: 5 }));
    await settle();
    expect(ids(s.stored.saved_filters), "V survives A's budget PUT").toEqual(["X", "V", "W"]);
  });
});

describe("a keepalive flush on a page that only hid", () => {
  it("a failed flush is retried, and a back/forward-cache return does not re-read over the unsent edit", async () => {
    const s = ctlStore(listOf("X"));
    const a = prefsDevice("dev-a", { debounceMs: 60_000, retryMs: 20 });
    await loaded(a.result);
    act(() => a.result.current.commit((cur) => withBudget(cur, "Groceries", 5, T(1))));
    s.down = true;
    window.dispatchEvent(new Event("pagehide"));
    await settle();                                                            // the flush failed
    s.down = false;
    window.dispatchEvent(new Event("pageshow"));
    await settle(5);
    expect(a.result.current.doc.budgets, "not re-read over").toEqual({ Groceries: 5 });
    expect(JSON.parse(localStorage.getItem("dev-a")!).budgets).toEqual({ Groceries: 5 });
    await waitFor(() => expect(s.stored.budgets, "the retry delivered it").toEqual({ Groceries: 5 }));
  });
  it("while a flush is still in flight, a return does not re-read", async () => {
    const s = ctlStore(listOf("X"));
    s.holdKeepalive = true;
    const a = prefsDevice("dev-a", { debounceMs: 60_000 });
    await loaded(a.result);
    act(() => a.result.current.commit((cur) => withBudget(cur, "Groceries", 5, T(1))));
    window.dispatchEvent(new Event("pagehide"));
    await waitFor(() => expect(s.heldKeepalive).toHaveLength(1));
    const n = gets(s).length;
    window.dispatchEvent(new Event("pageshow"));
    await settle();
    expect(gets(s).length, "no re-read under an in-flight flush").toBe(n);
    expect(a.result.current.doc.budgets).toEqual({ Groceries: 5 });
    s.heldKeepalive[0]();
  });
});

describe("a first load that failed is retried on a timer", () => {
  it("a tab that stays in front reconnects once the store answers, without any event", async () => {
    const s = ctlStore(listOf("X"));
    s.down = true;
    const a = await loaded(prefsDevice("dev-a", { retryMs: 10 }).result);
    expect(a.current.localOnly).toBe(true);
    s.down = false;
    await waitFor(() => expect(a.current.localOnly).toBe(false));
    expect(ids(a.current.doc.saved_filters)).toEqual(["X"]);
  });
});

describe("the re-read's own guards", () => {
  it("a storm of return events (visibility, focus, pageshow, online) makes ONE read, and an equal doc keeps its identity", async () => {
    const s = ctlStore(listOf("X"));
    const a = await loaded(prefsDevice("dev-a").result);
    const before = a.current.doc, n = gets(s).length;
    act(() => {
      comeBack();
      for (const ev of ["focus", "pageshow", "online"]) window.dispatchEvent(new Event(ev));
    });
    await settle(50);
    expect(gets(s).length - n).toBe(1);
    expect(puts(s)).toHaveLength(0);
    expect(a.current.doc).toBe(before);
  });
  it("a PUT in flight blocks the re-read (it would answer with the doc before this edit)", async () => {
    const held = heldStore({ updated_at: T(0), items: ["old"] });
    const h = await loaded(hook({ resyncOnVisible: true }));
    act(() => h.current.commit({ updated_at: T(1), items: ["mine"] }));
    await waitFor(() => expect(held).toHaveLength(1));                         // in flight
    comeBack();
    await settle();
    expect(h.current.doc.items).toEqual(["mine"]);
    held[0].answer(200, {});
  });
  it("a failing re-read is retried six times, each wait twice the last, then waits for the next return", async () => {
    const spy = vi.spyOn(globalThis, "setTimeout");
    try {
      let down = false, n = 0;
      vi.stubGlobal("fetch", async () => {
        if (down) { n += 1; throw new TypeError("network down"); }
        return new Response(JSON.stringify({ updated_at: T(0), items: [] }), { status: 200 });
      });
      await loaded(hook({ resyncOnVisible: true, retryMs: 3 }));
      down = true;
      comeBack();
      await settle(400);
      expect(n, "the read on return + six retries").toBe(7);
      const delays = spy.mock.calls.map((c) => c[1]).filter((d): d is number => typeof d === "number" && d >= 3 && d <= 96 && d % 3 === 0);
      expect(delays).toEqual([3, 6, 12, 24, 48, 96]);
    } finally { spy.mockRestore(); }
  });
});

describe("canonicalJson", () => {
  it("is blind to key order and nothing else", () => {
    expect(canonicalJson({ b: 1, a: { d: [2, { y: 1, x: 2 }], c: null } }))
      .toBe(canonicalJson({ a: { c: null, d: [2, { x: 2, y: 1 }] }, b: 1 }));
    expect(canonicalJson([1, 2])).not.toBe(canonicalJson([2, 1]));
  });
});

describe("unmount clears the saved indicator's reset", () => {
  it("a save that succeeds just before unmount schedules no state update after it", async () => {
    prefsStore();
    const hook = renderHook(() => useSyncedDoc<PrefsDoc>({ ...PREFS_SYNC, localKey: "unmount", debounceMs: 5 }));
    await loaded(hook.result);
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      await act(async () => { hook.result.current.commit((d) => withBudget(d, "Dining", 400, T(3))); });
      await waitFor(() => expect(hook.result.current.saveState).toBe("saved"));
      const cleared = vi.spyOn(globalThis, "clearTimeout");
      hook.unmount();
      expect(cleared).toHaveBeenCalled();
      expect(vi.getTimerCount()).toBe(0);
      cleared.mockRestore();
    } finally {
      vi.useRealTimers();
    }
  });
});

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { ManualAsset } from "../data/types";
import {
  acquiredQuestion, addValue, afterNewestReport, cardRows, changedEntries, classOptions, compareSaved, type ConfigView,
  configEntryOf, copyOf, countedAsSaved, type DraftContext, emptyManualAssets,
  forSave, hasChanges, isAssetId, isFirstValue, type LiveAsset, MAX_ASSETS, MAX_VALUES,
  type ManualAssetsDoc, mergeManualAssets, nameKey, newAssetId, normalizeManualAssets, pyStrip, rebaseDraft,
  refusalText, removeAsset, removedElsewhere, removeValue, signedFor, skippedById, TOMBSTONE_KEEP_DAYS, upsertAsset,
  validateDraft,
} from "./manualAssets";
import { SHIPPED_ASSET_CLASSES } from "./tokens";

const T = (min: number) => `2026-07-16T10:${String(min).padStart(2, "0")}:00.000Z`;
const asset = (pin: Partial<LiveAsset> = {}): LiveAsset => ({
  id: "a-000000000001", name: "Family car", owner: "Alex", asset_class: "Car", liquid: false, acquired: null,
  values: [{ as_of: "2026-03-01", value: 21000 }], updated_at: T(0), ...pin,
});
const doc = (...assets: ManualAssetsDoc["assets"]): ManualAssetsDoc => ({ version: 1, updated_at: T(0), assets });
const exported = (pin: Partial<ManualAsset> = {}): ManualAsset => ({
  id: "home", name: "Home", institution: "Example Realty", owner: "Joint", asset_class: "House", liquid: false,
  acquired: null, values: [{ as_of: "2025-01-01", value: 500000 }], origin: "config", ...pin,
});
const CTX: DraftContext = {
  owners: ["Alex", "Sam", "Joint"], classes: SHIPPED_ASSET_CLASSES, accountNames: ["Joint Checking"],
  configEntries: [{ id: "sedan", name: "Sedan" }],
};
const messages = (d: ManualAssetsDoc, ctx = CTX) => validateDraft(d, ctx).map((p) => `${p.field}${p.index ?? ""}: ${p.message}`);

describe("newAssetId", () => {
  it("is a- and 12 random hex characters, an id the store takes, never one already used", () => {
    const id = newAssetId();
    expect(id).toMatch(/^a-[0-9a-f]{12}$/);
    expect(isAssetId(id)).toBe(true);
    const seen = new Set(Array.from({ length: 200 }, () => newAssetId()));
    expect(seen.size).toBe(200);
    expect(newAssetId([id])).not.toBe(id);
  });
});

describe("isAssetId (the store's is_manual_id, the engine's is_id)", () => {
  it("runs of letters or digits of any script joined by single dashes, its own lowercase, at most 64 characters", () => {
    for (const ok of ["home", "a-0123456789ab", "family-home", "maison-été", "x".repeat(64)]) expect(isAssetId(ok), ok).toBe(true);
    for (const bad of ["", "Home", "a--b", "-a", "a-", "a_b", "a b", "x".repeat(65), 7]) expect(isAssetId(bad), String(bad)).toBe(false);
  });
});

describe("the pure operations", () => {
  it("upsertAsset replaces the entry of its id (a removal included) or adds one, stamping both", () => {
    const d = upsertAsset(doc(asset()), asset({ name: "Car" }), T(5));
    expect(d.assets).toEqual([asset({ name: "Car", updated_at: T(5) })]);
    expect(d.updated_at).toBe(T(5));
    const added = upsertAsset(d, asset({ id: "a-000000000002" }), T(6));
    expect(added.assets.map((a) => a.id)).toEqual(["a-000000000001", "a-000000000002"]);
    const back = upsertAsset(removeAsset(d, "a-000000000001", T(7)), asset(), T(8));
    expect(back.assets[0]).not.toHaveProperty("deleted_at");
  });
  it("addValue and removeValue change one asset's list; an unknown asset or place changes nothing", () => {
    const d = addValue(doc(asset()), "a-000000000001", { as_of: "2026-06-01", value: 20000 }, T(1));
    expect((d.assets[0] as LiveAsset).values).toEqual([{ as_of: "2026-03-01", value: 21000 }, { as_of: "2026-06-01", value: 20000 }]);
    expect((removeValue(d, "a-000000000001", 0, T(2)).assets[0] as LiveAsset).values).toEqual([{ as_of: "2026-06-01", value: 20000 }]);
    expect(addValue(d, "nope", { as_of: "2026-06-01", value: 1 }, T(3))).toBe(d);
    expect(removeValue(d, "a-000000000001", 5, T(3))).toBe(d);
  });
  it("removeAsset leaves a tombstone: its id and name, removed and stamped now, no values", () => {
    const d = removeAsset(doc(asset()), "a-000000000001", T(9));
    expect(d.assets).toEqual([{ id: "a-000000000001", name: "Family car", updated_at: T(9), deleted_at: T(9) }]);
    expect(removeAsset(d, "a-000000000001", T(10)), "a removal is removed once").toBe(d);
  });
  it("copyOf makes an app asset of a config entry's id and values, and carries no mark (the exports say it is a copy)", () => {
    const c = copyOf(exported({ id: "sedan", name: "Sedan", asset_class: "Car", acquired: "2026-02-12" }), T(1));
    expect(c).toEqual({ id: "sedan", name: "Sedan", owner: "Joint", asset_class: "Car", liquid: false,
      acquired: "2026-02-12", values: [{ as_of: "2025-01-01", value: 500000 }], updated_at: T(1) });
  });
  it("signedFor flips the sign only when the class crosses between an asset and a debt", () => {
    const v = [{ as_of: "2026-01-01", value: 1000 }, { as_of: "2026-02-01", value: null }];
    expect(signedFor(v, "Car", "Other Debt", SHIPPED_ASSET_CLASSES)).toEqual([{ as_of: "2026-01-01", value: -1000 }, { as_of: "2026-02-01", value: null }]);
    expect(signedFor(v, "", "Mortgage", SHIPPED_ASSET_CLASSES)[0].value, "a new asset's first class").toBe(-1000);
    expect(signedFor([{ as_of: "2026-01-01", value: -1000 }], "Mortgage", "Car", SHIPPED_ASSET_CLASSES)[0].value).toBe(1000);
    const negative = [{ as_of: "2026-01-01", value: -250 }];
    expect(signedFor(negative, "Car", "Private Stock", SHIPPED_ASSET_CLASSES), "asset to asset: as it is").toBe(negative);
    expect(signedFor(v, "Mortgage", "Other Debt", SHIPPED_ASSET_CLASSES), "debt to debt: as it is").toBe(v);
  });
  it("names compare as the engine compares them: Python's strip, then casefold", () => {
    expect(nameKey(" Straße ")).toBe(nameKey("STRASSE"));
    expect(pyStrip("\u001fHome\u0085")).toBe("Home");                  // Python strips these, JavaScript's trim does not
    expect(pyStrip("﻿Home"), "Python keeps a byte-order mark").toBe("﻿Home");
    expect(pyStrip("　Home ")).toBe("Home");
  });
});

describe("what a Save sends", () => {
  it("only the entries the draft changed are stamped with the save's instant; the rest go as stored, verbatim", () => {
    const stored = doc(asset(), asset({ id: "a-000000000002", name: "Boat", updated_at: T(1) }));
    const draft = upsertAsset(stored, asset({ name: "  Family car 2  ", values: [{ as_of: "2026-03-01", value: 21000.004 }] }), T(3));
    expect(changedEntries(stored, draft).map((a) => a.id)).toEqual(["a-000000000001"]);
    const out = forSave(stored, draft, T(9));
    expect(out.updated_at).toBe(T(9));
    expect(out.assets[0]).toEqual(asset({ name: "Family car 2", values: [{ as_of: "2026-03-01", value: 21000 }], updated_at: T(9) }));
    expect(out.assets[1], "untouched: the stored entry itself").toBe(stored.assets[1]);
  });
  it("an edit undone, a padded name or a re-stamp is no change; a removal of an unsaved asset is dropped", () => {
    const stored = doc(asset());
    expect(hasChanges(stored, upsertAsset(stored, asset({ name: "Family car " }), T(5)))).toBe(false);
    const added = upsertAsset(stored, asset({ id: "a-000000000009" }), T(5));
    expect(hasChanges(stored, added)).toBe(true);
    const undone = removeAsset(added, "a-000000000009", T(6));
    expect(hasChanges(stored, undone)).toBe(false);
    expect(forSave(stored, undone, T(7)).assets.map((a) => a.id)).toEqual(["a-000000000001"]);
  });
  it("a removal of a stored asset goes as a tombstone stamped with the save", () => {
    const stored = doc(asset());
    const out = forSave(stored, removeAsset(stored, "a-000000000001", T(2)), T(9));
    expect(out.assets).toEqual([{ id: "a-000000000001", name: "Family car", updated_at: T(9), deleted_at: T(9) }]);
  });
  it("a stored asset carrying an earlier bundle's from_config is no change, and drops the key once it is saved", () => {
    const marked = { ...asset(), from_config: true } as LiveAsset;
    const stored = doc(marked);
    expect(hasChanges(stored, stored)).toBe(false);
    expect(forSave(stored, stored, T(9)).assets[0], "untouched: sent as stored").toBe(marked);
    const out = forSave(stored, upsertAsset(stored, { ...marked, name: "Car" }, T(2)), T(9));
    expect(out.assets[0]).not.toHaveProperty("from_config");
  });
  it("values go oldest first, each to the cent", () => {
    const stored = doc();
    const draft = upsertAsset(stored, asset({ values: [{ as_of: "2026-05-01", value: 1.005 }, { as_of: "2026-01-01", value: 2 }] }), T(1));
    expect((forSave(stored, draft, T(2)).assets[0] as LiveAsset).values).toEqual([{ as_of: "2026-01-01", value: 2 }, { as_of: "2026-05-01", value: 1 }]);
  });
});

describe("rebaseDraft: a newer stored copy under unsaved edits", () => {
  it("an entry left as it was takes the newer copy; an edited one keeps the edit; new ones on either side stay", () => {
    const base = doc(asset(), asset({ id: "a-000000000002", name: "Boat" }));
    const draft = upsertAsset(base, asset({ name: "Car" }), T(2));
    const withNew = upsertAsset(draft, asset({ id: "a-000000000003", name: "Bike" }), T(2));
    const next = doc(asset({ name: "Car elsewhere", updated_at: T(4) }), asset({ id: "a-000000000002", name: "Boat 2", updated_at: T(4) }),
      asset({ id: "a-000000000004", name: "Van", updated_at: T(4) }));
    const out = rebaseDraft(base, withNew, next);
    expect(out.assets.map((a) => a.name)).toEqual(["Car", "Boat 2", "Van", "Bike"]);
  });
  it("an asset edited here and removed on another device meanwhile is named, and kept in the draft", () => {
    const base = doc(asset(), asset({ id: "a-000000000002", name: "Boat" }));
    const draft = upsertAsset(base, asset({ name: "Car" }), T(2));
    const next = doc(removeAsset(base, "a-000000000001", T(3)).assets[0], removeAsset(base, "a-000000000002", T(3)).assets[1]);
    expect(removedElsewhere(base, draft, next), "the untouched boat just goes").toEqual(["a-000000000001"]);
    expect(rebaseDraft(base, draft, next).assets.map((a) => [a.id, a.deleted_at ?? null])).toEqual([
      ["a-000000000001", null], ["a-000000000002", T(3)]]);
  });
});

describe("normalizeManualAssets and the load's merge", () => {
  it("keeps the entries the store would take, the first of each id, a tombstone as the store keeps one", () => {
    const raw = { version: 1, updated_at: T(1), assets: [
      asset(), asset({ name: "dup" }), { ...asset({ id: "a-000000000002" }), owner: "" },
      { id: "a-000000000003", name: "Gone", updated_at: T(1), deleted_at: T(1), values: [{ as_of: "x", value: 1 }] },
      asset({ id: "BAD" }), asset({ id: "a-000000000004", values: [] }), asset({ id: "a-000000000005", updated_at: "2026-07-16T10:00:00Z" }),
      { ...asset({ id: "a-000000000006" }), later_key: "kept" },
    ] };
    const d = normalizeManualAssets(raw);
    expect(d.assets.map((a) => a.id)).toEqual(["a-000000000001", "a-000000000003", "a-000000000006"]);
    expect(d.assets[1]).toEqual({ id: "a-000000000003", name: "Gone", updated_at: T(1), deleted_at: T(1) });
    expect(d.assets[2], "a key the dashboard does not know rides along").toHaveProperty("later_key", "kept");
    expect(normalizeManualAssets("garbage")).toEqual(emptyManualAssets());
  });
  const NOW = Date.parse(T(30));
  it("merges by id, the newer updated_at winning (the store's on a tie), sorted by id", () => {
    const local = doc(asset({ name: "mine", updated_at: T(5) }), asset({ id: "a-000000000002", name: "only here", updated_at: T(2) }));
    const server = { ...doc(asset({ name: "theirs", updated_at: T(3) }), asset({ id: "a-000000000000", name: "only there", updated_at: T(1) })), updated_at: T(6) };
    const m = mergeManualAssets(local, server, NOW);
    expect(m.assets.map((a) => a.name)).toEqual(["only there", "mine", "only here"]);
    expect(m.updated_at).toBe(T(6));
    const tie = mergeManualAssets(doc(asset({ name: "mine" })), doc(asset({ name: "theirs" })), NOW);
    expect(tie.assets[0].name).toBe("theirs");
  });
  it("an entry only this browser holds is kept within the store's keep time for removals, and dropped past it", () => {
    const ago = (days: number) => new Date(NOW - days * 86_400_000).toISOString();
    expect(TOMBSTONE_KEEP_DAYS).toBe(90);
    const local = doc(asset({ id: "a-00000000000a", name: "Sold boat", updated_at: ago(91) }),
      asset({ id: "a-00000000000b", name: "Offline edit", updated_at: ago(10) }));
    const m = mergeManualAssets(local, doc(), NOW);
    expect(m.assets.map((a) => a.name), "the 91-day-old one may be a removal the store forgot").toEqual(["Offline edit"]);
    // the store holding the id decides by stamps as before, however old
    const both = mergeManualAssets(doc(asset({ name: "old but newer", updated_at: ago(200) })),
      doc(asset({ name: "older", updated_at: ago(300) })), NOW);
    expect(both.assets[0].name).toBe("old but newer");
  });
});

describe("validateDraft: every rule the store and the engine apply", () => {
  it("a sound draft has no problem, and a removal is not checked", () => {
    expect(messages(doc(asset(), { id: "a-000000000002", name: "Old", updated_at: T(0), deleted_at: T(0) }))).toEqual([]);
  });
  it("the id must be one the store takes, and unique", () => {
    expect(messages(doc(asset({ id: "Not_An_Id" })))).toEqual(["id: Its id is not one the family store takes."]);
    expect(messages(doc(asset(), asset({ name: "Other" })))).toEqual(["doc: Two assets share the id a-000000000001."]);
  });
  it("a name: 1 to 80 characters once trimmed, no control character, no account's and no other counted asset's", () => {
    expect(messages(doc(asset({ name: "   " })))).toEqual(["name: Give it a name."]);
    expect(messages(doc(asset({ name: "x".repeat(81) })))).toEqual(["name: A name is at most 80 characters."]);
    expect(messages(doc(asset({ name: "x".repeat(80) })))).toEqual([]);
    expect(messages(doc(asset({ name: "Car\nboat" })))).toEqual(["name: A name cannot hold a line break or another control character."]);
    expect(messages(doc(asset({ name: " joint checking " })))).toEqual(["name: An account already has this name: choose another."]);
    expect(messages(doc(asset({ name: "SEDAN" })))).toEqual(["name: Another manual asset has this name: choose another."]);
    // a copy of the entry overrides it, so its name is free; two app assets sharing a name both say so
    expect(messages(doc(asset({ id: "sedan", name: "Sedan" })))).toEqual([]);
    expect(messages(doc(asset(), asset({ id: "a-000000000002", name: "family car" })))).toEqual([
      "name: Another manual asset has this name: choose another.", "name: Another manual asset has this name: choose another."]);
    // compared as the engine compares (strip, casefold)
    expect(messages(doc(asset({ name: "STRASSE" }), asset({ id: "a-000000000002", name: "Straße\u0085" }))))
      .toEqual(["name: Another manual asset has this name: choose another.", "name: Another manual asset has this name: choose another."]);
  });
  it("a config entry an app asset hides counts again once that asset is removed, so its name is taken again", () => {
    // the hidden entry "Sedan" (id sedan) and an app asset of another id named "Sedan": fine while the copy lives
    const hidden: DraftContext = { ...CTX, configEntries: [{ id: "sedan", name: "Sedan" }] };
    const copy = asset({ id: "sedan", name: "Family sedan" });
    const twin = asset({ id: "a-000000000002", name: "Sedan" });
    expect(messages(doc(copy, twin), hidden)).toEqual([]);
    // the copy removed: the entry is back, and the engine would leave the app asset of its name out
    expect(messages(removeAsset(doc(copy, twin), "sedan", T(1)), hidden))
      .toEqual(["name: Another manual asset has this name: choose another."]);
  });
  it("the owner is one of the household's, the class one the dashboard knows", () => {
    expect(messages(doc(asset({ owner: "" })))).toEqual(["owner: Choose who owns it."]);
    expect(messages(doc(asset({ owner: "Robin" })))).toEqual(["owner: Choose one of the household's owners."]);
    expect(messages(doc(asset({ asset_class: "" })))).toEqual(["asset_class: Choose a class."]);
    expect(messages(doc(asset({ asset_class: "Yacht" })))).toEqual(["asset_class: Choose one of the classes the dashboard knows."]);
  });
  it("an owner or class label the store would refuse (over 80 characters, or a control character) says so", () => {
    const long = "A".repeat(81);
    const owners: DraftContext = { ...CTX, owners: [...CTX.owners, long, "Tab\tname"] };
    expect(messages(doc(asset({ owner: long })), owners))
      .toEqual(["owner: This owner's name is longer than the family store takes (80 characters)."]);
    expect(messages(doc(asset({ owner: "Tab\tname" })), owners))
      .toEqual(["owner: This owner's name holds a line break or another control character, which the family store refuses."]);
    const classes: DraftContext = { ...CTX, classes: { ...SHIPPED_ASSET_CLASSES, assets: [...SHIPPED_ASSET_CLASSES.assets, long] } };
    expect(messages(doc(asset({ asset_class: long })), classes))
      .toEqual(["asset_class: This class is longer than the family store takes (80 characters)."]);
  });
  it("a debt is never liquid, and its values are amounts owed (below zero)", () => {
    expect(messages(doc(asset({ asset_class: "Other Debt", liquid: true, values: [{ as_of: "2026-03-01", value: -5 }] }))))
      .toEqual(["liquid: A debt is never liquid."]);
    expect(messages(doc(asset({ asset_class: "Cash", liquid: true })))).toEqual([]);
    expect(messages(doc(asset({ asset_class: "Mortgage", values: [{ as_of: "2026-03-01", value: 5 }] }))))
      .toEqual(["value0: This is above zero, so net worth would count this debt as an asset: type the amount owed again."]);
  });
  it("1 to 120 values, each on a real day with a figure within a trillion dollars, one per day", () => {
    expect(messages(doc(asset({ values: [] })))).toEqual(["values: Add at least one value."]);
    const many = Array.from({ length: MAX_VALUES + 1 }, (_, i) => ({ as_of: `20${10 + Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}-01`, value: 1 }));
    expect(messages(doc(asset({ values: many })))).toEqual(["values: At most 120 values per asset: remove some before saving."]);
    expect(messages(doc(asset({ values: many.slice(0, MAX_VALUES) })))).toEqual([]);
    expect(messages(doc(asset({ values: [{ as_of: "2026-02-30", value: 1 }] })))).toEqual(["as_of0: Enter the date of this value."]);
    expect(messages(doc(asset({ values: [{ as_of: "", value: 1 }] })))).toEqual(["as_of0: Enter the date of this value."]);
    expect(messages(doc(asset({ values: [{ as_of: "2026-03-01", value: null }] })))).toEqual(["value0: Enter the value."]);
    expect(messages(doc(asset({ asset_class: "Mortgage", values: [{ as_of: "2026-03-01", value: null }] }))))
      .toEqual(["value0: Enter the amount owed."]);
    expect(messages(doc(asset({ values: [{ as_of: "2026-03-01", value: 1e12 + 1 }] }))))
      .toEqual(["value0: A value is at most a trillion dollars either way."]);
    expect(messages(doc(asset({ values: [{ as_of: "2026-03-01", value: 1 }, { as_of: "2026-03-01", value: 2 }] }))))
      .toEqual(["as_of1: Another value has this date: keep one value per day."]);
  });
  it("acquired is empty or a real day, and not after the first value", () => {
    expect(messages(doc(asset({ acquired: "2026-03-01" })))).toEqual([]);
    expect(messages(doc(asset({ acquired: "2026-13-01" })))).toEqual(["acquired: Enter the day it was acquired as a date, or leave it empty."]);
    expect(messages(doc(asset({ acquired: "2026-03-02" }))))
      .toEqual(["acquired: It cannot be acquired after its first value: make it Mar 1, 2026 or earlier, or leave it empty."]);
  });
  it("at most 50 live assets (removals do not count), and a document the store's size cap takes", () => {
    const fifty = Array.from({ length: MAX_ASSETS }, (_, i) => asset({ id: `a-${String(i).padStart(12, "0")}`, name: `Asset ${i}` }));
    const gone = { id: "a-zzzzzzzzzzzz", name: "Old", updated_at: T(0), deleted_at: T(0) };
    expect(messages(doc(...fifty, gone))).toEqual([]);
    expect(messages(doc(...fifty, asset({ id: "a-fffffffffff1", name: "One more" })))).toEqual(["doc: At most 50 assets: remove one before saving."]);
    const huge = asset({ name: "Big", values: Array.from({ length: 120 }, (_, i) => ({ as_of: `${2000 + Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}-01`, value: 1 })) });
    const big = doc(...Array.from({ length: 50 }, (_, i) => ({ ...huge, id: `a-${String(i).padStart(12, "0")}`, name: `Big ${i}`, padding: "x".repeat(8000) })));
    expect(messages(big).at(-1)).toBe("doc: The list is too large to save: remove some values or assets.");
  });
});

describe("the acquired question and the dates past the accounts' newest report", () => {
  it("asks only when the first value is after the data's first day and no acquisition is stated", () => {
    expect(acquiredQuestion(asset({ values: [{ as_of: "2026-03-01", value: 1 }] }), "2024-07-01")).toBe("2026-03-01");
    expect(acquiredQuestion(asset({ values: [{ as_of: "2024-07-01", value: 1 }] }), "2024-07-01")).toBeNull();
    expect(acquiredQuestion(asset({ acquired: "2026-03-01" }), "2024-07-01")).toBeNull();
    expect(acquiredQuestion(asset(), null), "no data: nothing to compare with").toBeNull();
  });
  it("finds the earliest later value or acquisition dated after the newest report", () => {
    const a = asset({ values: [{ as_of: "2026-12-01", value: 1 }, { as_of: "2026-11-20", value: 1 }, { as_of: "2026-01-01", value: 1 }] });
    expect(afterNewestReport(a, "2026-10-31")).toBe("2026-11-20");
    expect(afterNewestReport({ ...a, values: [{ as_of: "2026-01-01", value: 1 }], acquired: "2026-11-02" }, "2026-10-31")).toBe("2026-11-02");
    expect(afterNewestReport(a, null)).toBeNull();
    expect(afterNewestReport(asset(), "2026-10-31")).toBeNull();
  });
  it("a first value is never one: the engine carries it back, so it counts at once whatever its date", () => {
    expect(afterNewestReport(asset({ values: [{ as_of: "2026-10-31", value: 1 }] }), "2026-09-30"), "dated today").toBeNull();
    expect(afterNewestReport(asset({ values: [{ as_of: "2027-01-01", value: 1 }] }), "2026-10-31"), "dated ahead").toBeNull();
    const two = asset({ values: [{ as_of: "2026-11-20", value: 1 }, { as_of: "2026-12-01", value: 1 }] });
    expect(afterNewestReport(two, "2026-10-31"), "the second value waits; the first does not").toBe("2026-12-01");
    expect(isFirstValue(two, "2026-11-20")).toBe(true);
    expect(isFirstValue(two, "2026-12-01")).toBe(false);
  });
});

describe("classOptions", () => {
  it("the dashboard's own order, the assets and the debts apart, a class it does not know after", () => {
    const o = classOptions({ assets: ["Car", "Cash", "Boats", "House"], liabilities: ["Other Debt", "Mortgage"] });
    expect(o).toEqual({ assets: ["Cash", "House", "Car", "Boats"], liabilities: ["Mortgage", "Other Debt"] });
  });
});

describe("cardRows: the exports with the draft over them", () => {
  const home = exported({ id: "family-home", origin: "app", institution: null });
  const sedan = exported({ id: "sedan", name: "Sedan", asset_class: "Car", owner: "Sam" });
  const homeAsset = asset({ id: "family-home", name: "Home", owner: "Joint", asset_class: "House" });
  const view = (exportedList: ManualAsset[], overridden: ManualAsset[] = []): ConfigView =>
    ({ exported: exportedList, overridden });
  const kinds = (rows: ReturnType<typeof cardRows>) => rows.map((r) => [r.kind, r.id]);
  const entryOf = (rows: ReturnType<typeof cardRows>, id: string) => {
    const r = rows.find((x) => x.id === id)!;
    return r.kind === "app" || r.kind === "removal" ? r.entry?.name ?? null : "n/a";
  };
  it("an app asset of the draft is editable, a config entry is read-only, in the exports' order", () => {
    const d = doc(homeAsset);
    expect(kinds(cardRows(view([home, sedan]), d, d))).toEqual([["app", "family-home"], ["config", "sedan"]]);
    expect(entryOf(cardRows(view([home, sedan]), d, d), "family-home"), "an asset of its own id is no copy").toBeNull();
  });
  // the six states of a copy
  it("a copy just made: the export still counts the entry, so it is a copy; undone, the entry's row is back", () => {
    const stored = doc(homeAsset);
    const copied = upsertAsset(stored, copyOf(sedan, T(1)), T(1));
    const rows = cardRows(view([home, sedan]), copied, stored);
    expect(kinds(rows)).toEqual([["app", "family-home"], ["app", "sedan"]]);
    expect(entryOf(rows, "sedan")).toBe("Sedan");
    expect(kinds(cardRows(view([home, sedan]), removeAsset(copied, "sedan", T(2)), stored))).toEqual([
      ["app", "family-home"], ["config", "sedan"]]);
  });
  it("a copy saved, not ingested yet: still the entry's id in the export, so still a copy", () => {
    const stored = doc(homeAsset, copyOf(sedan, T(1)));
    expect(entryOf(cardRows(view([home, sedan]), stored, stored), "sedan")).toBe("Sedan");
  });
  it("a copy saved and ingested: the entry is hidden (manual_overridden), named with its value; removing it shows what returns", () => {
    const stored = doc(homeAsset, copyOf(sedan, T(1)));
    const ingested = view([home, { ...sedan, origin: "app", institution: null }], [sedan]);
    expect(entryOf(cardRows(ingested, stored, stored), "sedan")).toBe("Sedan");
    const unsaved = cardRows(ingested, removeAsset(stored, "sedan", T(2)), stored);
    expect(unsaved.find((r) => r.id === "sedan")).toMatchObject({ kind: "removal", saved: false, entry: sedan });
    // saved, and not ingested yet: a row the documents alone give
    const removed = removeAsset(stored, "sedan", T(2));
    expect(cardRows(ingested, removed, removed).find((r) => r.id === "sedan")).toMatchObject({ kind: "removal", saved: true, entry: sedan });
  });
  it("the config entry later removed from accounts.yaml: the id is in neither list, so a plain app asset", () => {
    const stored = doc(homeAsset, copyOf(sedan, T(1)));
    const rows = cardRows(view([home, { ...sedan, origin: "app", institution: null }]), stored, stored);
    expect(entryOf(rows, "sedan")).toBeNull();
    expect(configEntryOf("sedan", view([home]))).toBeNull();
  });
  it("a copy the engine left out: the export counts the entry again, so it is still a copy", () => {
    const stored = doc(homeAsset, { ...copyOf(sedan, T(1)), owner: "Nobody" });
    expect(entryOf(cardRows(view([home, sedan]), stored, stored), "sedan")).toBe("Sedan");
  });
  it("a removal saved: pending while the exports count the app asset; a copy's removal is the entry's row once they list the entry", () => {
    const removed = removeAsset(doc(homeAsset), "family-home", T(1));
    expect(cardRows(view([home]), removed, removed)).toEqual([
      { kind: "removal", id: "family-home", name: "Home", saved: true, entry: null }]);
    // the copy ingested (the export counts it as an app asset, the entry hidden): its removal is pending
    const copyGone = removeAsset(doc(copyOf(sedan, T(1))), "sedan", T(2));
    const ingestedCopy = view([{ ...sedan, origin: "app", institution: null }], [sedan]);
    expect(cardRows(ingestedCopy, copyGone, copyGone)).toEqual([
      { kind: "removal", id: "sedan", name: "Sedan", saved: true, entry: sedan }]);
    // the exports list the config's entry (the copy never ingested, or its removal ingested): the entry's own row
    expect(kinds(cardRows(view([sedan]), copyGone, copyGone))).toEqual([["config", "sedan"]]);
  });
  it("a copy removed on a device whose clock runs a day ahead of the exports' build: the entry's own row, no pending one", () => {
    const aDayAhead = "2026-07-17T10:02:00.000Z";
    const copyGone = removeAsset(doc(copyOf(sedan, T(1))), "sedan", aDayAhead);
    expect(cardRows(view([sedan]), copyGone, copyGone)).toEqual([{ kind: "config", id: "sedan", name: "Sedan", entry: sedan }]);
  });
  it("an asset the exports count that the store holds nothing for is gone", () => {
    expect(kinds(cardRows(view([home]), doc(), doc()))).toEqual([["gone", "family-home"]]);
  });
  it("a new asset keeps its place at the end while its name is typed", () => {
    const d = upsertAsset(doc(homeAsset), asset({ id: "a-000000000009", name: "Aardvark" }), T(1));
    expect(cardRows(view([home]), d, doc(homeAsset)).map((r) => r.id)).toEqual(["family-home", "a-000000000009"]);
  });
});

describe("after the ingest: what the exports say of the save", () => {
  const checks = [{ name: "manual_assets.entry", status: "warn" as const,
    detail: "manual asset 'a-000000000002' from the app left out: its owner is not one of the household's owners" }];
  it("reads the engine's warnings by id", () => {
    expect(skippedById([...checks, { name: "manual_assets.entry", status: "warn", detail: "manual asset #3 from the app left out: it is not an object" }]))
      .toEqual(new Map([["a-000000000002", "its owner is not one of the household's owners"]]));
  });
  it("up to date when every saved asset is counted with its name and values and every removal is gone", () => {
    const saved = doc(asset(), asset({ id: "a-000000000002", name: "Boat" }), asset({ id: "a-000000000003", name: "Bike" }));
    const out = [exported({ id: "a-000000000001", name: "Family car", owner: "Alex", asset_class: "Car", origin: "app",
      values: [{ as_of: "2026-03-01", value: 21000 }] }),
      exported({ id: "gone", origin: "app" })];
    const r = compareSaved(saved, ["gone"], out, checks);
    expect(r.upToDate).toBe(false);
    expect(Object.fromEntries(r.byId)).toEqual({
      "a-000000000001": { state: "counted" },
      "a-000000000002": { state: "skipped", why: "its owner is not one of the household's owners" },
      "a-000000000003": { state: "not-yet" },
      gone: { state: "still-counted" },
    });
    expect(compareSaved(doc(asset()), [], [out[0]], []).upToDate).toBe(true);
    expect(compareSaved(doc(asset({ values: [{ as_of: "2026-03-01", value: 21001 }] })), [], [out[0]], []).upToDate,
      "a value the exports do not hold yet").toBe(false);
  });
  it("counted as saved means every exported field: the owner, the class, the liquid flag and the day acquired too", () => {
    const saved = asset({ acquired: "2026-01-01" });
    const counted = exported({ id: saved.id, name: saved.name, owner: "Alex", asset_class: "Car", liquid: false,
      acquired: "2026-01-01", values: [{ as_of: "2026-03-01", value: 21000 }], origin: "app" });
    expect(countedAsSaved(saved, counted)).toBe(true);
    for (const [key, other] of [["owner", "Sam"], ["asset_class", "House"], ["liquid", true], ["acquired", null]] as const) {
      expect(countedAsSaved(saved, { ...counted, [key]: other }), key).toBe(false);
    }
  });
});

describe("the card's words for a refusal", () => {
  it("the store's reason in plain words; a stamp too far ahead says the device's clock looks wrong", () => {
    expect(refusalText(400, "at most 50 assets: nothing was written")).toBe(
      "Not saved: the family store refused it — at most 50 assets: nothing was written. Change what it names and save again.");
    expect(refusalText(400, "'a-0123456789ab': an asset's timestamp is in the future: check this device's clock")).toBe(
      "Not saved: this device's clock looks wrong — the family store found a time more than a day ahead of its own. "
      + "Correct the clock, then save again.");
    expect(refusalText(413, null)).toBe("Not saved: the list is larger than the family store takes. Remove some values or assets, then save again.");
  });
});

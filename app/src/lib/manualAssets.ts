// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The assets entered in the app (a home, a vehicle, anything no account reports), as the family store keeps them at
// /api/manual-assets (deploy/scenario_store.py; the ETL reads the file on ingest, etl/manual_assets.py), and the
// editor's rules over them. The document is {version: 1, updated_at, assets: [...]}: a live asset has an id, a name,
// an owner, a class, a liquid flag, an optional day it was acquired and its dated values (dollars, negative for a
// debt); a removed one is a tombstone (its id, name and stamps, with `deleted_at` set), which keeps an older copy in
// another tab from bringing it back. The store merges a save by asset id, the newer `updated_at` winning, so a save
// carries the whole document and never removes by leaving an asset out.
//
// An app asset with the id of an accounts.yaml `manual_entries` item overrides that entry; removing it brings the
// entry back (etl/manual_assets.py effective). The exports say which entries are hidden so (accounts.json
// `manual_overridden`), so an asset is a copy of a config entry while its id is one of those, or one the exports still
// count as the config's (a copy not ingested yet, or one the engine left out); a key an earlier bundle wrote for it
// (`from_config`) is neither read nor kept.
import { EPOCH_ISO } from "../data/useSyncedDoc";
import type { AssetClasses, ManualAsset, QualityReport } from "../data/types";
import { storageKey } from "./demo";
import { fmtDate } from "./format";
import { CLASS_COLORS, SHIPPED_ASSET_CLASSES, UNKNOWN_COLOR } from "./tokens";

/** this browser's mirror of the document (data/useManualAssetsStore.ts) */
export const MANUAL_ASSETS_LOCAL_KEY = storageKey("ff.manual-assets.v1");

// The store's limits (deploy/scenario_store.py; tests/test_scenario_store.py pins the store's side).
/** live assets the store holds; a save past it is refused, nothing evicted (removed ones do not count) */
export const MAX_ASSETS = 50;
/** dated values per asset */
export const MAX_VALUES = 120;
/** an id's and a name's longest, in characters as sent */
export const MAX_ID_LEN = 64;
export const MAX_NAME_LEN = 80;
/** an owner's or a class's longest */
const MAX_LABEL_LEN = 80;
/** a value's bound either way, in dollars (the database's cents overflow past it) */
export const MAX_VALUE = 1e12;
/** the largest document the store takes, in bytes of its JSON */
export const MAX_DOC_BYTES = 512 * 1024;
/** how long the store keeps a removal (a tombstone) after its `updated_at` before it forgets it: an entry this browser
 *  holds that the store does not, and older than this, may be one the store has removed and forgotten */
export const TOMBSTONE_KEEP_DAYS = 90;

/** one dated value; `value` is null only in the editor before a figure is typed (validateDraft refuses it) */
export interface AssetValue { as_of: string; value: number | null }

/** a live asset of the document. A key the dashboard does not know rides along untouched. */
export interface LiveAsset {
  id: string;
  name: string;
  owner: string;
  asset_class: string;
  liquid: boolean;
  /** the day it was acquired: before it the asset is not held; null or absent: held as far back as the data goes */
  acquired?: string | null;
  values: AssetValue[];
  updated_at: string;
  deleted_at?: null;
}

/** a removed asset: what the store keeps of it (deploy/scenario_store.py TOMBSTONE_KEYS) */
export interface Tombstone { id: string; name: string; updated_at: string; deleted_at: string }

export type StoredAsset = LiveAsset | Tombstone;

export interface ManualAssetsDoc { version: 1; updated_at: string; assets: StoredAsset[] }

export const emptyManualAssets = (): ManualAssetsDoc => ({ version: 1, updated_at: EPOCH_ISO, assets: [] });

export const isLive = (a: StoredAsset): a is LiveAsset => a.deleted_at === undefined || a.deleted_at === null;

// An id: runs of letters or digits of any script joined by single dashes, its own lowercase (the store's is_manual_id
// and the engine's is_id). \p{L}\p{N} stands for Python's [^\W_].
const ID = /^[\p{L}\p{N}]+(?:-[\p{L}\p{N}]+)*$/u;
// a C0 control character or DEL (the store's and the engine's has_control): matching them is the point
// eslint-disable-next-line no-control-regex
const CONTROL = /[\x00-\x1f\x7f]/;
const DAY = /^\d{4}-\d{2}-\d{2}$/;
/** the stamp the store takes: UTC with milliseconds, one width (Date.toISOString) */
const STAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;

/** characters as the store counts them (code points, not UTF-16 units) */
const chars = (s: string): number => [...s].length;

// What Python's str.strip() removes (every character str.isspace() is true for), at either end: the engine trims names
// with it. JavaScript's trim() differs at the edges (it removes U+FEFF and keeps U+001C-U+001F and U+0085).
const PY_SPACE = "\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const PY_STRIP = new RegExp(`^[${PY_SPACE}]+|[${PY_SPACE}]+$`, "g");
/** `s` trimmed as the engine trims a name (Python's strip) */
export const pyStrip = (s: string): string => s.replace(PY_STRIP, "");
/** a name as the engine compares two (etl/manual_assets.py: strip, then casefold): folding to upper then lower case
 *  makes "Straße" and "STRASSE" one name, as casefold does */
export const nameKey = (s: string): string => pyStrip(s).toUpperCase().toLowerCase();

export const isAssetId = (v: unknown): v is string =>
  typeof v === "string" && chars(v) <= MAX_ID_LEN && v === v.toLowerCase() && ID.test(v);

/** a day written YYYY-MM-DD that names a real date */
export function isDay(v: unknown): v is string {
  if (typeof v !== "string" || !DAY.test(v)) return false;
  const [y, m, d] = v.split("-").map(Number);
  const t = new Date(Date.UTC(y, m - 1, d));
  return t.getUTCFullYear() === y && t.getUTCMonth() === m - 1 && t.getUTCDate() === d;
}

const isStamp = (v: unknown): v is string => typeof v === "string" && STAMP.test(v) && !Number.isNaN(Date.parse(v));
const isLabel = (v: unknown): v is string =>
  typeof v === "string" && pyStrip(v) !== "" && chars(v) <= MAX_LABEL_LEN && !CONTROL.test(v);
const isAmount = (v: unknown): v is number =>
  typeof v === "number" && Number.isFinite(v) && Math.abs(v) <= MAX_VALUE;
const isName = (v: unknown): v is string =>
  typeof v === "string" && pyStrip(v) !== "" && chars(v) <= MAX_NAME_LEN && !CONTROL.test(v);

/** whether the store would take `a` as an entry of the document (deploy/scenario_store.py _asset_problem): the shape
 *  only, never the household's meaning */
function storeTakes(a: unknown): a is StoredAsset {
  if (!a || typeof a !== "object" || Array.isArray(a)) return false;
  const r = a as Record<string, unknown>;
  if (!isAssetId(r.id) || !isName(r.name) || !isStamp(r.updated_at)) return false;
  if (r.deleted_at !== undefined && r.deleted_at !== null) return isStamp(r.deleted_at);
  if (!isLabel(r.owner) || !isLabel(r.asset_class) || typeof r.liquid !== "boolean") return false;
  if (r.acquired !== undefined && r.acquired !== null && !isDay(r.acquired)) return false;
  return Array.isArray(r.values) && r.values.length > 0 && r.values.length <= MAX_VALUES
    && r.values.every((v) => !!v && typeof v === "object" && isDay((v as AssetValue).as_of) && isAmount((v as AssetValue).value));
}

/** Any input (the store's reply, this browser's mirror, garbage) as a document: the entries the store would take, the
 *  first of each id; a tombstone kept as the store keeps one (its id, name and stamps). An entry the store would refuse
 *  is left out, so no save of this document carries it (a save that held one would be refused whole). */
export function normalizeManualAssets(raw: unknown): ManualAssetsDoc {
  const doc = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
  const seen = new Set<string>();
  const assets: StoredAsset[] = [];
  for (const a of Array.isArray(doc.assets) ? doc.assets : []) {
    if (!storeTakes(a) || seen.has(a.id)) continue;
    seen.add(a.id);
    assets.push(isLive(a) ? a : { id: a.id, name: a.name, updated_at: a.updated_at, deleted_at: a.deleted_at });
  }
  const at = doc.updated_at;
  return { version: 1, updated_at: typeof at === "string" && (at === EPOCH_ISO || isStamp(at)) ? at : EPOCH_ISO, assets };
}

/** The load's merge of this browser's copy with the store's (data/useManualAssetsStore.ts): every id of either, the
 *  entry with the newer `updated_at` winning (the store's on a tie), sorted by id as the store keeps them — the store's
 *  own merge (merge_manual_assets), so an edit that never reached it is not lost to a newer document. An entry only
 *  this browser holds is kept only while its `updated_at` is within TOMBSTONE_KEEP_DAYS of `now` (ms): an older one may
 *  be an asset another device removed long enough ago that the store has forgotten the removal, and sending it back
 *  would bring the asset back. */
export function mergeManualAssets(local: ManualAssetsDoc, server: ManualAssetsDoc, now: number): ManualAssetsDoc {
  const byId = new Map<string, StoredAsset>();
  for (const a of server.assets) byId.set(a.id, a);
  const oldest = now - TOMBSTONE_KEEP_DAYS * 86_400_000;
  for (const a of local.assets) {
    const theirs = byId.get(a.id);
    if (theirs ? a.updated_at > theirs.updated_at : Date.parse(a.updated_at) >= oldest) byId.set(a.id, a);
  }
  return {
    version: 1,
    updated_at: local.updated_at > server.updated_at ? local.updated_at : server.updated_at,
    assets: [...byId.values()].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)),
  };
}

/** A new asset's id: `a-` and 12 random hex characters, none of `taken` */
export function newAssetId(taken: Iterable<string> = []): string {
  const used = new Set(taken);
  for (;;) {
    const bytes = new Uint8Array(6);
    crypto.getRandomValues(bytes);
    const id = `a-${[...bytes].map((b) => b.toString(16).padStart(2, "0")).join("")}`;
    if (!used.has(id)) return id;
  }
}

const stamped = (doc: ManualAssetsDoc, assets: StoredAsset[], now: string): ManualAssetsDoc =>
  ({ ...doc, updated_at: now, assets });

/** `asset` in place of the entry of its id (live or removed), or added at the end; stamped `now` */
export function upsertAsset(doc: ManualAssetsDoc, asset: LiveAsset, now: string): ManualAssetsDoc {
  const next = { ...asset, updated_at: now };
  const at = doc.assets.findIndex((a) => a.id === asset.id);
  return stamped(doc, at < 0 ? [...doc.assets, next] : doc.assets.map((a, i) => (i === at ? next : a)), now);
}

/** one more dated value on the live asset `id` (unchanged when there is none) */
export function addValue(doc: ManualAssetsDoc, id: string, value: AssetValue, now: string): ManualAssetsDoc {
  const a = doc.assets.find((x) => x.id === id);
  return a && isLive(a) ? upsertAsset(doc, { ...a, values: [...a.values, value] }, now) : doc;
}

/** the live asset `id` without its value at `index` (unchanged when there is none) */
export function removeValue(doc: ManualAssetsDoc, id: string, index: number, now: string): ManualAssetsDoc {
  const a = doc.assets.find((x) => x.id === id);
  if (!a || !isLive(a) || index < 0 || index >= a.values.length) return doc;
  return upsertAsset(doc, { ...a, values: a.values.filter((_, i) => i !== index) }, now);
}

/** the asset `id` removed: a tombstone in its place (its id and name, `deleted_at` and `updated_at` now), which beats
 *  an older copy of it anywhere; for an override of a config entry, the entry counts again */
export function removeAsset(doc: ManualAssetsDoc, id: string, now: string): ManualAssetsDoc {
  const a = doc.assets.find((x) => x.id === id);
  if (!a || !isLive(a)) return doc;
  const tomb: Tombstone = { id: a.id, name: pyStrip(a.name) || a.id, updated_at: now, deleted_at: now };
  return stamped(doc, doc.assets.map((x) => (x.id === id ? tomb : x)), now);
}

/** an asset as it is saved: its name trimmed, each amount to the cent, the values oldest first, and without the
 *  `from_config` key an earlier bundle wrote (the exports say which assets are copies now) */
function prepared(a: StoredAsset): StoredAsset {
  if (!isLive(a)) return a;
  const values = a.values.map((v) => ({ as_of: v.as_of, value: v.value === null ? null : Math.round(v.value * 100) / 100 }))
    .sort((x, y) => (x.as_of < y.as_of ? -1 : x.as_of > y.as_of ? 1 : 0));
  const { from_config: _dropped, ...rest } = a as LiveAsset & { from_config?: unknown };
  return { ...rest, name: pyStrip(a.name), values };
}

/** what an entry says, without the stamps that only order saves */
function content(a: StoredAsset): string {
  if (!isLive(a)) return JSON.stringify({ id: a.id, removed: true });
  const { updated_at: _u, deleted_at: _d, ...rest } = prepared(a) as LiveAsset;
  return JSON.stringify(Object.fromEntries(Object.entries(rest).sort(([x], [y]) => (x < y ? -1 : 1))));
}

/** the draft's entries that say something the stored document does not: a new or edited live asset, or a removal of
 *  one the store holds live (a removal of an asset the store never held changes nothing) */
export function changedEntries(stored: ManualAssetsDoc, draft: ManualAssetsDoc): StoredAsset[] {
  const before = new Map(stored.assets.map((a) => [a.id, a]));
  return draft.assets.filter((a) => {
    const was = before.get(a.id);
    if (!isLive(a)) return was !== undefined && isLive(was);
    return was === undefined || content(was) !== content(a);
  });
}

export const hasChanges = (stored: ManualAssetsDoc, draft: ManualAssetsDoc): boolean =>
  changedEntries(stored, draft).length > 0;

/** The document a Save sends: every stored entry the draft leaves as it was, verbatim (its stamps included, so the
 *  store's merge leaves it alone), and every changed one prepared (name trimmed, amounts to the cent) and stamped
 *  `now`, the instant of the save, so the newest save of an asset wins it anywhere. A removal of an asset the store
 *  never held is dropped. */
export function forSave(stored: ManualAssetsDoc, draft: ManualAssetsDoc, now: string): ManualAssetsDoc {
  const changed = new Set(changedEntries(stored, draft).map((a) => a.id));
  const before = new Map(stored.assets.map((a) => [a.id, a]));
  const assets: StoredAsset[] = [];
  for (const a of draft.assets) {
    if (changed.has(a.id)) {
      assets.push(isLive(a) ? { ...(prepared(a) as LiveAsset), updated_at: now } : { ...a, updated_at: now, deleted_at: now });
    } else {
      const was = before.get(a.id);
      if (was) assets.push(was);
    }
  }
  for (const a of stored.assets) if (!draft.assets.some((d) => d.id === a.id)) assets.push(a);
  return { version: 1, updated_at: changed.size ? now : stored.updated_at, assets };
}

/** The draft carried over a newer stored document (an echo, another device's save): an entry the draft left as it was
 *  in `base` takes the newer one, an entry it changed keeps its change, and an entry new on either side is kept. */
export function rebaseDraft(base: ManualAssetsDoc, draft: ManualAssetsDoc, next: ManualAssetsDoc): ManualAssetsDoc {
  const was = new Map(base.assets.map((a) => [a.id, a]));
  const mine = new Map(draft.assets.map((a) => [a.id, a]));
  const untouched = (id: string) => {
    const b = was.get(id), d = mine.get(id);
    return b !== undefined && d !== undefined && content(b) === content(d);
  };
  const assets: StoredAsset[] = next.assets.map((a) => (mine.has(a.id) && !untouched(a.id) ? mine.get(a.id)! : a));
  for (const a of draft.assets) {
    if (!next.assets.some((n) => n.id === a.id) && !untouched(a.id)) assets.push(a);
  }
  return { ...next, assets };
}

// ------------------------------------------------------------------------------------------------ classes and owners

/** the household's classes in the dashboard's own order (lib/tokens.ts SHIPPED_ASSET_CLASSES, the allocation donut's),
 *  the assets and the debts apart; a class the export names that the dashboard does not know comes after, in the
 *  export's order */
export function classOptions(classes: AssetClasses): AssetClasses {
  const known = (mine: readonly string[], theirs: readonly string[]) =>
    [...mine.filter((c) => theirs.includes(c)), ...theirs.filter((c) => !mine.includes(c))];
  return { assets: known(SHIPPED_ASSET_CLASSES.assets, classes.assets),
           liabilities: known(SHIPPED_ASSET_CLASSES.liabilities, classes.liabilities) };
}

/** a class's colour, the one every chart gives it */
export const classColor = (cls: string): string => CLASS_COLORS[cls] ?? UNKNOWN_COLOR;

/** a debt class: its value is an amount owed, stored negative, and it is never liquid */
export const isDebtClass = (cls: string, classes: AssetClasses): boolean => classes.liabilities.includes(cls);

/** `values` moved from the class `from` to `to`: a move from an asset class to a debt class makes the amounts owed
 *  (negative), one from a debt to an asset makes them positive, and a move within either side leaves them as they are
 *  (an asset's negative figure stays negative) */
export function signedFor(values: AssetValue[], from: string, to: string, classes: AssetClasses): AssetValue[] {
  const was = isDebtClass(from, classes), debt = isDebtClass(to, classes);
  if (was === debt) return values;
  return values.map((v) => ({ ...v, value: v.value === null ? null : debt ? -Math.abs(v.value) : Math.abs(v.value) }));
}

// ------------------------------------------------------------------------------------------------------ validation

/** a field of an asset a problem is tied to; `doc` for the document as a whole */
export type ProblemField = "id" | "name" | "owner" | "asset_class" | "liquid" | "acquired" | "values" | "as_of" | "value";

export interface Problem {
  /** the asset; null for a problem of the whole document */
  id: string | null;
  field: ProblemField | "doc";
  /** the value's place in the asset's list, for `as_of` and `value` */
  index?: number;
  message: string;
}

export interface DraftContext {
  /** assumptions.json `owners` */
  owners: readonly string[];
  /** assumptions.json `asset_classes` */
  classes: AssetClasses;
  /** the registry accounts' names (accounts.json): no asset may take one */
  accountNames: readonly string[];
  /** every config entry: those the last ingest counted (accounts.json `manual_assets` from the config) and those an
   *  app asset hid (`manual_overridden`), each counted once no live asset of its id is left */
  configEntries: readonly Pick<ManualAsset, "id" | "name">[];
}

/** Every problem the store or the engine would find with the draft, in words a person can act on, each tied to its
 *  asset and field — the store's shape rules (deploy/scenario_store.py validate_manual_assets: the id, a name of 1 to 80
 *  characters with no control character, an owner and a class of at most 80 characters with none either, a liquid
 *  flag, a real day for `acquired` and for each value, 1 to 120 values each within a trillion dollars either way, at
 *  most 50 live assets, a document of at most 512 KiB) and the engine's meaning (etl/manual_assets.py effective: the
 *  owner one of the household's, the class one it knows, a debt never liquid, `acquired` not after the first value, a
 *  name that is no account's and no other counted asset's, compared as the engine compares them, `nameKey`; a config
 *  entry hidden now counts again once no live asset of its id is left). Two rules are the editor's own: two values on
 *  one day, which the engine would settle silently by keeping the later, and a debt's value above zero, which the
 *  engine counts as an asset (with a warning). Removed assets are not checked. */
export function validateDraft(draft: ManualAssetsDoc, ctx: DraftContext): Problem[] {
  const out: Problem[] = [];
  const live = draft.assets.filter(isLive);
  const classes = [...ctx.classes.assets, ...ctx.classes.liabilities];
  const accounts = new Set(ctx.accountNames.map(nameKey));
  const liveIds = new Set(live.map((a) => a.id));
  // a config entry an app asset of its id overrides is not counted, so its name is free
  const configNames = new Set(ctx.configEntries.filter((e) => !liveIds.has(e.id)).map((e) => nameKey(e.name)));
  const ids = new Map<string, number>();
  for (const a of draft.assets) ids.set(a.id, (ids.get(a.id) ?? 0) + 1);
  for (const [id, n] of ids) if (n > 1) out.push({ id: null, field: "doc", message: `Two assets share the id ${id}.` });
  if (live.length > MAX_ASSETS) {
    out.push({ id: null, field: "doc", message: `At most ${MAX_ASSETS} assets: remove one before saving.` });
  }
  const names = new Map<string, number>();
  for (const a of live) names.set(nameKey(a.name), (names.get(nameKey(a.name)) ?? 0) + 1);

  for (const a of live) {
    const at = (field: ProblemField, message: string, index?: number) =>
      out.push(index === undefined ? { id: a.id, field, message } : { id: a.id, field, index, message });
    if (!isAssetId(a.id)) at("id", "Its id is not one the family store takes.");
    const name = pyStrip(a.name);
    if (name === "") at("name", "Give it a name.");
    else if (chars(name) > MAX_NAME_LEN) at("name", `A name is at most ${MAX_NAME_LEN} characters.`);
    else if (CONTROL.test(a.name)) at("name", "A name cannot hold a line break or another control character.");
    else if (accounts.has(nameKey(name))) at("name", "An account already has this name: choose another.");
    else if (configNames.has(nameKey(name)) || (names.get(nameKey(name)) ?? 0) > 1) {
      at("name", "Another manual asset has this name: choose another.");
    }
    // the store refuses a label longer than 80 characters or holding a control character, whatever the household's
    // config accepts: such a save would be refused whole
    const label = (v: string, what: string) => (chars(v) > MAX_LABEL_LEN
      ? `This ${what} is longer than the family store takes (${MAX_LABEL_LEN} characters).`
      : CONTROL.test(v) ? `This ${what} holds a line break or another control character, which the family store refuses.` : null);
    if (a.owner === "") at("owner", "Choose who owns it.");
    else if (label(a.owner, "owner's name")) at("owner", label(a.owner, "owner's name")!);
    else if (!ctx.owners.includes(a.owner)) at("owner", "Choose one of the household's owners.");
    const debt = isDebtClass(a.asset_class, ctx.classes);
    if (a.asset_class === "") at("asset_class", "Choose a class.");
    else if (label(a.asset_class, "class")) at("asset_class", label(a.asset_class, "class")!);
    else if (!classes.includes(a.asset_class)) at("asset_class", "Choose one of the classes the dashboard knows.");
    if (a.liquid && debt) at("liquid", "A debt is never liquid.");
    if (a.values.length === 0) at("values", "Add at least one value.");
    if (a.values.length > MAX_VALUES) at("values", `At most ${MAX_VALUES} values per asset: remove some before saving.`);
    const days = new Map<string, number>();
    a.values.forEach((v, i) => {
      if (!isDay(v.as_of)) at("as_of", "Enter the date of this value.", i);
      else if (days.has(v.as_of)) at("as_of", "Another value has this date: keep one value per day.", i);
      else days.set(v.as_of, i);
      if (v.value === null) at("value", debt ? "Enter the amount owed." : "Enter the value.", i);
      else if (!isAmount(v.value)) at("value", "A value is at most a trillion dollars either way.", i);
      else if (debt && v.value > 0) {
        at("value", "This is above zero, so net worth would count this debt as an asset: type the amount owed again.", i);
      }
    });
    const acquired = a.acquired ?? null;
    if (acquired !== null) {
      if (!isDay(acquired)) at("acquired", "Enter the day it was acquired as a date, or leave it empty.");
      else {
        const first = firstDay(a);
        if (first !== null && acquired > first) {
          at("acquired", `It cannot be acquired after its first value: make it ${fmtDate(first)} or earlier, or leave it empty.`);
        }
      }
    }
  }
  if (new TextEncoder().encode(JSON.stringify(draft)).length > MAX_DOC_BYTES) {
    out.push({ id: null, field: "doc", message: "The list is too large to save: remove some values or assets." });
  }
  return out;
}

/** the asset's first value's day (the earliest valid one), or null */
export function firstDay(a: Pick<LiveAsset, "values">): string | null {
  const days = a.values.map((v) => v.as_of).filter(isDay).sort();
  return days[0] ?? null;
}

/** The day the acquired prompt asks about: the first value's, when it is after the data's first day (`dataFirst`,
 *  Indexes.dataRange.first) and the asset states no acquisition. Before it the asset is carried back at that value
 *  (nothing to do if it was owned already); "acquired on that day" sets `acquired` instead. null: no question. */
export function acquiredQuestion(a: Pick<LiveAsset, "values" | "acquired">, dataFirst: string | null): string | null {
  if (dataFirst === null || (a.acquired ?? null) !== null) return null;
  const first = firstDay(a);
  return first !== null && first > dataFirst ? first : null;
}

/** The earliest of the asset's later value days (every value but its first) and its acquisition that is after
 *  `newestReport` (the newest balance any account reported): net worth dates nothing past the accounts' newest report,
 *  so such a date takes effect once the accounts report on or after it. The first value is not one of them: the
 *  engine carries it back, so it counts at once whatever its date. null when none is, or no account has reported. */
export function afterNewestReport(a: Pick<LiveAsset, "values" | "acquired">, newestReport: string | null): string | null {
  if (newestReport === null) return null;
  const first = firstDay(a);
  const later = a.values.map((v) => v.as_of).filter((d) => isDay(d) && d !== first);
  const days = [...later, ...(a.acquired ? [a.acquired] : [])].filter((d) => isDay(d) && d > newestReport);
  return days.sort()[0] ?? null;
}

/** whether the value dated `day` is the asset's first (the earliest valid date): the one the engine carries back, so
 *  it counts at once whatever its date */
export function isFirstValue(a: Pick<LiveAsset, "values">, day: string): boolean {
  return isDay(day) && firstDay(a) === day;
}

// ------------------------------------------------------------------------------------------- the card and the ingest

/** what the exports say of the config's entries: those the last ingest counted and those an app asset hid */
export interface ConfigView {
  /** accounts.json `manual_assets`, the effective list */
  exported: readonly ManualAsset[];
  /** accounts.json `manual_overridden`: the entries an app asset hid at the last ingest */
  overridden: readonly ManualAsset[];
}

/** The config entry an asset of `id` copies, or null: the entry it hid at the last ingest (`manual_overridden`), or the
 *  one the exports still count under its id (a copy not ingested yet, or one the engine left out). An id in neither is
 *  no copy, whatever it was once: the entry has left accounts.yaml. */
export function configEntryOf(id: string, view: Pick<ConfigView, "exported" | "overridden">): ManualAsset | null {
  return view.overridden.find((e) => e.id === id)
    ?? view.exported.find((e) => e.id === id && e.origin === "config") ?? null;
}

/** a row of the card, in the exports' order (by name, ignoring case, then id), an asset added in the draft last */
export type CardRow =
  /** a config entry no app asset overrides: read-only, with Edit a copy */
  | { kind: "config"; id: string; name: string; entry: ManualAsset }
  /** an app asset (saved or not): editable. `entry`: the config entry it copies (configEntryOf), null for one of its
   *  own; `exported`: what the last ingest counted under its id */
  | { kind: "app"; id: string; name: string; asset: LiveAsset; entry: ManualAsset | null; exported: ManualAsset | null }
  /** an app asset removed — in the draft (`saved` false), or saved while the exports still count it as an app asset: it
   *  leaves the dashboard after the next ingest, and for a copy (`entry`) the config's own value counts again */
  | { kind: "removal"; id: string; name: string; saved: boolean; entry: ManualAsset | null }
  /** counted at the last ingest, but the store does not hold it: the next ingest leaves it out */
  | { kind: "gone"; id: string; name: string; entry: ManualAsset };

/** The card's list: the exports' effective list with the draft over it — every live asset of the draft, every config
 *  entry it does not override, an app asset removed (in the draft, or saved while the exports still count it as an app
 *  asset: a row the documents alone give, so it is there after a reload), and an asset the exports count that the
 *  store holds no entry for. A saved removal of a copy whose id the exports list as the config's entry is that entry's
 *  own row: the config's value is what counts, whatever this device's clock said when it was removed. `stored` is the
 *  copy the store acknowledged, which tells a removal in the draft from one already saved. */
export function cardRows(view: ConfigView, draft: ManualAssetsDoc, stored: ManualAssetsDoc): CardRow[] {
  const { exported } = view;
  const inDraft = new Map(draft.assets.map((a) => [a.id, a]));
  const inStore = new Map(stored.assets.map((a) => [a.id, a]));
  const exportedById = new Map(exported.map((e) => [e.id, e]));
  const rows: CardRow[] = [];
  const taken = new Set<string>();
  for (const a of draft.assets) {
    if (isLive(a)) {
      rows.push({ kind: "app", id: a.id, name: a.name, asset: a, entry: configEntryOf(a.id, view),
                  exported: exportedById.get(a.id) ?? null });
      taken.add(a.id);
      continue;
    }
    const was = inStore.get(a.id), counted = exportedById.get(a.id);
    if (was && isLive(was)) {
      rows.push({ kind: "removal", id: a.id, name: was.name, saved: false, entry: configEntryOf(a.id, view) });
    } else if (was && counted?.origin === "app") {
      rows.push({ kind: "removal", id: a.id, name: was.name, saved: true, entry: configEntryOf(a.id, view) });
    } else continue;
    taken.add(a.id);
  }
  for (const e of exported) {
    if (taken.has(e.id)) continue;
    if (e.origin === "config") rows.push({ kind: "config", id: e.id, name: e.name, entry: e });
    else if (!inDraft.has(e.id)) rows.push({ kind: "gone", id: e.id, name: e.name, entry: e });
  }
  // A row keeps its place while its name is edited: it sorts by the name the store or the exports hold for it, and an
  // asset neither holds yet (added in the draft) comes last, in the order it was added.
  const settled = (r: CardRow): string | null => {
    const s = stored.assets.find((a) => a.id === r.id);
    const name = s && isLive(s) ? s.name : exportedById.get(r.id)?.name ?? (r.kind === "app" ? null : r.name);
    return name === null ? null : nameKey(name);
  };
  const keyed = rows.map((r, i) => ({ r, i, k: settled(r) }));
  keyed.sort((x, y) => {
    if (x.k === null || y.k === null) return x.k === null && y.k === null ? x.i - y.i : x.k === null ? 1 : -1;
    return x.k < y.k ? -1 : x.k > y.k ? 1 : x.r.id < y.r.id ? -1 : x.r.id > y.r.id ? 1 : 0;
  });
  return keyed.map((x) => x.r);
}

/** An app asset made from a config entry (Edit a copy): the entry's id, name, owner, class, flag, acquisition and
 *  values. The entry's holder (`institution`) has no place in the document: the copy reads as entered by hand. */
export const copyOf = (e: ManualAsset, now: string): LiveAsset => ({
  id: e.id, name: e.name, owner: e.owner, asset_class: e.asset_class, liquid: e.liquid, acquired: e.acquired,
  values: e.values.map((v) => ({ ...v })), updated_at: now,
});

/** the asset ids the store now holds as removed that the draft still holds live, edited, and `base` held live: removed
 *  on another device while this browser's draft was open (saving the draft brings each back) */
export function removedElsewhere(base: ManualAssetsDoc, draft: ManualAssetsDoc, next: ManualAssetsDoc): string[] {
  const was = new Map(base.assets.map((a) => [a.id, a]));
  const mine = new Map(draft.assets.map((a) => [a.id, a]));
  return next.assets.filter((a) => {
    const b = was.get(a.id), d = mine.get(a.id);
    return !isLive(a) && b !== undefined && isLive(b) && d !== undefined && isLive(d) && content(b) !== content(d);
  }).map((a) => a.id);
}

const ENTRY_WARNING = /^manual asset '([^']*)' from the app left out: (.*)$/s;

/** why the last ingest left each app asset out, by id: the `manual_assets.entry` warnings of the quality report
 *  (etl/manual_assets.py words each "manual asset '<id>' from the app left out: <why>") */
export function skippedById(checks: QualityReport["checks"]): Map<string, string> {
  const out = new Map<string, string>();
  for (const c of checks) {
    if (c.name !== "manual_assets.entry") continue;
    const m = ENTRY_WARNING.exec(c.detail);
    if (m) out.set(m[1], m[2]);
  }
  return out;
}

/** the `manual_assets.load` warning's detail: the last ingest could not read the document at all */
export const loadProblem = (checks: QualityReport["checks"]): string | null =>
  checks.find((c) => c.name === "manual_assets.load")?.detail ?? null;

const cents = (v: number | null) => (v === null ? null : Math.round(v * 100));

/** whether the exports count the live asset as it was saved: every field the export carries — the name, the owner,
 *  the class, the liquid flag, the day it was acquired and the values (days and cents) */
export function countedAsSaved(a: LiveAsset, exported: ManualAsset | undefined): boolean {
  if (!exported || exported.origin !== "app") return false;
  const mine = prepared(a) as LiveAsset;
  return exported.name === mine.name && exported.owner === mine.owner && exported.asset_class === mine.asset_class
    && exported.liquid === mine.liquid && exported.acquired === (mine.acquired ?? null)
    && mine.values.length === exported.values.length
    && mine.values.every((v, i) => v.as_of === exported.values[i].as_of && cents(v.value) === cents(exported.values[i].value));
}

// ---------------------------------------------------------------------------------------------- after a save
// (the follow of the ingest a save asks for is the shared one: lib/ingestFollow.ts)

/** What the card says while the store has refused the current document: its own reason in plain words; a stamp
 *  refused as too far ahead (deploy/scenario_store.py: "... in the future: check this device's clock") says that this
 *  device's clock looks wrong. */
export function refusalText(status: number, reason: string | null): string {
  if (reason !== null && /in the future/.test(reason)) {
    return "Not saved: this device's clock looks wrong — the family store found a time more than a day ahead of its own. "
      + "Correct the clock, then save again.";
  }
  if (reason !== null) return `Not saved: the family store refused it — ${reason}. Change what it names and save again.`;
  return status === 413 ? "Not saved: the list is larger than the family store takes. Remove some values or assets, then save again."
    : "Not saved: the family store refused it. Change the list, then save again.";
}

/** what the reloaded exports say of one saved asset */
export type SavedState = "counted" | "skipped" | "not-yet" | "removed" | "still-counted";

/** After a save and the ingest that followed it: for every live asset of `saved`, whether accounts.json
 *  `manual_assets` counts it as saved, why the ingest left it out (its `manual_assets.entry` warning), or that it is
 *  not in the dashboard yet; for every removal in `removed`, whether the exports still count it. `upToDate` when every
 *  one is as saved. */
export function compareSaved(saved: ManualAssetsDoc, removed: readonly string[], exported: readonly ManualAsset[],
  checks: QualityReport["checks"]): { upToDate: boolean; byId: Map<string, { state: SavedState; why?: string }> } {
  const byExport = new Map(exported.map((e) => [e.id, e]));
  const skipped = skippedById(checks);
  const byId = new Map<string, { state: SavedState; why?: string }>();
  for (const a of saved.assets.filter(isLive)) {
    const why = skipped.get(a.id);
    byId.set(a.id, countedAsSaved(a, byExport.get(a.id)) ? { state: "counted" }
      : why !== undefined ? { state: "skipped", why } : { state: "not-yet" });
  }
  for (const id of removed) {
    byId.set(id, { state: byExport.get(id)?.origin === "app" ? "still-counted" : "removed" });
  }
  return { upToDate: [...byId.values()].every((s) => s.state === "counted" || s.state === "removed"), byId };
}

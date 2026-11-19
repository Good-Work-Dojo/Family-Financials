// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// In-app preferences: budgets, the target mix, asset-class targets, projection defaults, payroll facts and the
// modules switched on — the *opinions* layer. config/assumptions.yaml stays the seed (household.yaml for the modules);
// prefs win client-side (merged over assumptions.json in DataContext) so
// editing them never requires YAML + `make ingest`. Persisted via ./api/prefs
// (last-write-wins, except the saved lists and the sections kept whole: data/usePrefsStore.ts PREFS_SYNC).
import { EPOCH_ISO } from "../data/useSyncedDoc";
import type { Assumptions, CategoryRename } from "../data/types";
import { mapCategoryName } from "./categoryRenames";
import { storageKey } from "./demo";
import { isModuleId, type ModuleId } from "./modules";
import { parseReportSpec, type ReportSpec } from "./reportSpec";
import { parseTargetMix, type TargetMix } from "./targetMix";

export const PREFS_LOCAL_KEY = storageKey("ff.prefs.v1");

/** One employment phase for the gross-savings-rate model. */
export interface PayrollPhase {
  person: string;
  from: string;               // YYYY-MM — first month the phase covers
  to: string | null;          // YYYY-MM inclusive, null = ongoing
  gross_annual: number;       // $/yr gross salary
  employee_401k_pct: number;  // % of gross deferred pre-tax
  employer_match_pct: number; // % of gross matched by the employer
}

/** What every saved item (a saved view now, a saved report) carries; each kind adds its own payload. */
export interface SavedBase { id: string; name: string; created_at: string }

/** A saved view: the Transactions tab's filter query (serializeFilters output), saved for the family. */
export interface SavedFilter extends SavedBase { query: string }

/** A saved report: a Spending → Reports spec (its filters, group-by, interval, measure and chart), saved for the family. */
export interface SavedReport extends SavedBase { spec: ReportSpec }

/** per list (saved views, saved reports): the prefs doc rides a 64 KB server cap (deploy/scenario_store.py) */
export const MAX_SAVED = 50;
/** the longest name the Save box takes (an input maxLength; the ops refuse more rather than truncate) */
export const MAX_SAVED_NAME = 80;
/** one saved item, as JSON, may not exceed this many characters. It is what stops 50 long queries from
 *  filling the 64 KB doc, and it is half the store's own per-entry bound (MAX_SAVED_ENTRY_BYTES, 4096),
 *  so an entry this app writes is never one the store refuses. A query is percent-encoded ASCII, so
 *  characters and bytes agree; the headroom covers a non-ASCII name. */
export const MAX_SAVED_ENTRY_CHARS = 2048;

/** The AI consent: whether AI categorization is on, the version of what is sent (the policy) the family agreed to, and
 *  when. Kept and carried whole; Settings → AI categorization reads and writes it (withAi). */
export interface AiPrefs {
  enabled: boolean;
  consent_policy: number | null;
  /** who receives the vendor names and which accounts are asked about, as agreed to (ai_status.json `scope`) */
  consent_scope: string | null;
  consented_at: string | null;
}
/** the longest consent scope the store keeps (deploy/scenario_store.py AI_SCOPE_MAX) */
export const AI_SCOPE_MAX = 200;
/** the AI section as it reads when none is stored, or the stored one is malformed: off, nothing consented */
export const AI_OFF: Readonly<AiPrefs> = { enabled: false, consent_policy: null, consent_scope: null, consented_at: null };

export interface PrefsDoc {
  version: 1;
  updated_at: string;
  budgets: Record<string, number>;
  /** asset-class targets, % of net worth (Investments shows their card only while some are set) */
  targets: Record<string, number>;
  /** the family's target mix, % of investable assets (lib/targetMix.ts); null = none saved yet */
  target_mix: TargetMix | null;
  projections: Record<string, number>;
  payroll: { phases: PayrollPhase[] };
  saved_filters: SavedFilter[];
  reports: SavedReport[];
  /** module id -> on, as the family switched it (lib/modules.ts effectiveModules: it wins over the config's seed); a
   *  module it never switched is absent. An id this bundle does not know (a later bundle's module) is carried through
   *  every save and ignored for effect, so this tab never erases another bundle's switch */
  modules: Record<string, boolean>;
  ai: AiPrefs;
}

/** the prefs sections that are saved-item lists (`saved_filters` and `reports`; a new list joins them the
 *  moment PrefsDoc declares it): the generic ops below take one of these as the item kind */
export type SavedKey = { [K in keyof PrefsDoc]: PrefsDoc[K] extends SavedBase[] ? K : never }[keyof PrefsDoc];
/** every SavedKey, as the family store names them (deploy/scenario_store.py PREFS_LIST_SECTIONS, pinned in
 *  tests/test_scenario_store.py): the sections a prefs PUT may omit, which the store then keeps as stored */
export const SAVED_KEYS = ["saved_filters", "reports"] as const satisfies readonly SavedKey[];
/** the prefs sections that are one value the store keeps whole when a PUT omits them (deploy/scenario_store.py
 *  PREFS_KEPT_WHOLE, pinned in tests/test_scenario_store.py): the target mix, the modules and the AI consent. A bundle
 *  that predates one never sends it, so its save leaves the stored copy alone. */
export const KEPT_WHOLE_KEYS = ["target_mix", "modules", "ai"] as const satisfies readonly (keyof PrefsDoc)[];
/** what a caller supplies to save one item of a kind: everything but the timestamp the op stamps */
export type SavedInput<K extends SavedKey> = Omit<PrefsDoc[K][number], "created_at">;

/** The only projection keys prefs may override — flat scalars of
 *  assumptions.projections (nested objects stay YAML-managed). The home's value is none of them: it is the snapshot's
 *  House-class rows (lib/lifeSummary homeValue), so an override a family stored for it before is dropped when the doc
 *  is read (normalizePrefs) and never merged (mergeAssumptions), and the next save writes the doc without it. */
export const PROJECTION_PREF_KEYS = [
  "inflation_pct", "withdrawal_rate_pct", "ltcg_pct", "state_income_tax_pct",
] as const;

/** `projections` with only the keys prefs may override (PROJECTION_PREF_KEYS); the same instance when it has no other */
function knownProjections(projections: Record<string, number>): Record<string, number> {
  const known = (PROJECTION_PREF_KEYS as readonly string[]);
  if (Object.keys(projections).every((k) => known.includes(k))) return projections;
  return Object.fromEntries(Object.entries(projections).filter(([k]) => known.includes(k)));
}

export function emptyPrefs(): PrefsDoc {
  return { version: 1, updated_at: EPOCH_ISO,
           budgets: {}, targets: {}, target_mix: null, projections: {}, payroll: { phases: [] }, saved_filters: [], reports: [],
           modules: {}, ai: { ...AI_OFF } };
}

function numericRecord(raw: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  if (raw && typeof raw === "object") {
    for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
      if (typeof v === "number" && Number.isFinite(v)) out[k] = v;
    }
  }
  return out;
}

/** One saved-item list out of anything: a non-list is empty; an entry that is not an object with a
 *  string id, name and created_at, whose kind payload `payload` refuses (null), or that is over
 *  MAX_SAVED_ENTRY_CHARS is dropped with ONE console.warn naming `section`; an entry repeating an id
 *  already seen is dropped silently (the same view twice is not malformed, just redundant). Unknown
 *  keys never survive: the result is rebuilt from the known fields. `payload` returns the kind's own
 *  fields (a view's `query`, a report's `spec`) or null. Saved reports reuse this, never a copy. */
export function normalizeSaved<T extends SavedBase>(
  raw: unknown, section: string,
  payload: (entry: Record<string, unknown>) => Omit<T, keyof SavedBase> | null,
): T[] {
  const out: T[] = [];
  if (!Array.isArray(raw)) return out;
  const seen = new Set<string>();
  for (const e of raw as unknown[]) {
    const r = e && typeof e === "object" ? e as Record<string, unknown> : null;
    const rest = r && typeof r.id === "string" && typeof r.name === "string" && typeof r.created_at === "string"
      ? payload(r) : null;
    if (!r || !rest) {
      console.warn(`prefs: dropping a malformed ${section} entry`);
      continue;
    }
    const item = { id: r.id, name: r.name, created_at: r.created_at, ...rest } as T;
    if (seen.has(item.id)) continue;
    if (JSON.stringify(item).length > MAX_SAVED_ENTRY_CHARS) {
      console.warn(`prefs: dropping an oversize ${section} entry (over ${MAX_SAVED_ENTRY_CHARS} characters)`);
      continue;
    }
    seen.add(item.id);
    out.push(item);
  }
  return out;
}

const savedFilters = (raw: unknown): SavedFilter[] =>
  normalizeSaved<SavedFilter>(raw, "saved_filters", (e) => (typeof e.query === "string" ? { query: e.query } : null));

/** a stored report keeps only a spec parseReportSpec accepts: an unknown groupBy / interval / measure /
 *  chart, or a non-filter key in its filters, drops the entry with the usual warning */
const savedReports = (raw: unknown): SavedReport[] =>
  normalizeSaved<SavedReport>(raw, "reports", (e) => {
    const spec = parseReportSpec(e.spec);
    return spec ? { spec } : null;
  });

/** the saved target mix out of anything: absent or null is none saved (an older doc), anything parseTargetMix refuses is
 *  dropped with ONE console.warn and reads as none saved */
function targetMix(raw: unknown): TargetMix | null {
  if (raw === undefined || raw === null) return null;
  const mix = parseTargetMix(raw);
  if (!mix) console.warn("prefs: dropping a malformed target_mix");
  return mix;
}

/** the most module switches the prefs doc holds, and the longest id: the store's own bounds (deploy/scenario_store.py
 *  MAX_MODULES and MAX_MODULE_ID_LEN, pinned in tests/test_scenario_store.py), so a carried section never makes a save
 *  the store refuses */
export const MAX_MODULES = 16;
export const MAX_MODULE_ID_LEN = 40;

/** the modules the family switched, out of anything: every id of at most MAX_MODULE_ID_LEN characters whose value is
 *  true or false, the ids this bundle knows (lib/modules.ts MODULE_IDS) first, then the others in their order, at most
 *  MAX_MODULES. An id it does not know is carried, not dropped: a later bundle's switch survives this tab's saves, and
 *  only lib/modules.ts effectiveModules decides what an id does (nothing, for one it does not know). */
function moduleSwitches(raw: unknown): Record<string, boolean> {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const kept = Object.entries(raw as Record<string, unknown>)
    .filter((e): e is [string, boolean] => e[0].length <= MAX_MODULE_ID_LEN && typeof e[1] === "boolean");
  return Object.fromEntries([...kept.filter(([id]) => isModuleId(id)), ...kept.filter(([id]) => !isModuleId(id))]
    .slice(0, MAX_MODULES));
}

/** the AI section out of anything: exactly `enabled` a boolean, `consent_policy` a whole number (never a boolean) or
 *  null, `consent_scope` a string of at most AI_SCOPE_MAX characters or null, `consented_at` a string or null — all
 *  four and no other key, as the store checks it and the ETL reads it (etl/ai/cache.py), so a hand-edited section
 *  reads the same everywhere; anything else reads as AI_OFF.
 *  One difference JSON hides from the browser: a policy written as `1.0` parses as the number 1 here, so it reads as
 *  on, while the store and the ETL see a float and read it as off; the step then stays off, so it fails closed. */
function aiPrefs(raw: unknown): AiPrefs {
  const a = raw && typeof raw === "object" && !Array.isArray(raw) ? raw as Record<string, unknown> : {};
  if (Object.keys(a).length !== 4) return { ...AI_OFF };
  const { enabled, consent_policy: policy, consent_scope: scope, consented_at: at } = a;
  if (typeof enabled !== "boolean") return { ...AI_OFF };
  if (!(policy === null || (typeof policy === "number" && Number.isInteger(policy)))) return { ...AI_OFF };
  if (!(scope === null || (typeof scope === "string" && scope.length <= AI_SCOPE_MAX))) return { ...AI_OFF };
  if (!(at === null || typeof at === "string")) return { ...AI_OFF };
  return { enabled, consent_policy: policy, consent_scope: scope, consented_at: at };
}

export function normalizePrefs(raw: unknown): PrefsDoc {
  const doc = (raw ?? {}) as Partial<PrefsDoc>;
  const phases: PayrollPhase[] = [];
  const rawPhases = (doc.payroll as { phases?: unknown[] } | undefined)?.phases;
  if (Array.isArray(rawPhases)) {
    for (const p of rawPhases as PayrollPhase[]) {
      if (!p || typeof p !== "object") continue;
      if (typeof p.person !== "string" || typeof p.from !== "string") continue;
      if (typeof p.gross_annual !== "number") continue;
      phases.push({
        person: p.person, from: p.from,
        to: typeof p.to === "string" ? p.to : null,
        gross_annual: p.gross_annual,
        employee_401k_pct: typeof p.employee_401k_pct === "number" ? p.employee_401k_pct : 0,
        employer_match_pct: typeof p.employer_match_pct === "number" ? p.employer_match_pct : 0,
      });
    }
  }
  const projections = knownProjections(numericRecord(doc.projections));
  return {
    version: 1,
    updated_at: typeof doc.updated_at === "string" ? doc.updated_at : EPOCH_ISO,
    budgets: numericRecord(doc.budgets),
    targets: numericRecord(doc.targets),
    target_mix: targetMix((doc as { target_mix?: unknown }).target_mix),
    projections,
    payroll: { phases },
    saved_filters: savedFilters((doc as { saved_filters?: unknown }).saved_filters),
    reports: savedReports((doc as { reports?: unknown }).reports),
    modules: moduleSwitches((doc as { modules?: unknown }).modules),
    ai: aiPrefs((doc as { ai?: unknown }).ai),
  };
}

// -------------------------------------------------------------- pure ops

function bump(doc: PrefsDoc, patch: Partial<PrefsDoc>, nowISO: string): PrefsDoc {
  return { ...doc, ...patch, version: 1, updated_at: nowISO };
}

/** value null → clear back to the auto/YAML default. `renames` (assumptions.category_renames): a
 *  budget saved under a category's OLD name is the same budget (renameBudgets moves it on read), so
 *  setting or clearing the new name also drops every old-name key that maps to it - left behind, a
 *  cleared budget would rename back and resurrect, and a set one would sit shadowed under a stale key. */
export function withBudget(doc: PrefsDoc, category: string, value: number | null,
                           nowISO: string, renames: readonly CategoryRename[] = []): PrefsDoc {
  const budgets = { ...doc.budgets };
  if (renames.length) {
    for (const k of Object.keys(budgets)) if (k !== category && mapCategoryName(k, renames) === category) delete budgets[k];
  }
  if (value === null || !Number.isFinite(value)) delete budgets[category];
  else budgets[category] = value;
  return bump(doc, { budgets }, nowISO);
}

export function withTarget(doc: PrefsDoc, assetClass: string, pct: number | null,
                           nowISO: string): PrefsDoc {
  const targets = { ...doc.targets };
  if (pct === null || !Number.isFinite(pct)) delete targets[assetClass];
  else targets[assetClass] = pct;
  return bump(doc, { targets }, nowISO);
}

/** The saved target mix, or null when `mix` is not one parseTargetMix accepts (nothing to write: the store would keep
 *  refusing a doc that carries it). */
export function withTargetMix(doc: PrefsDoc, mix: TargetMix, nowISO: string): PrefsDoc | null {
  const ok = parseTargetMix(mix);
  return ok ? bump(doc, { target_mix: ok }, nowISO) : null;
}

export function withProjection(doc: PrefsDoc, key: string, value: number | null,
                               nowISO: string): PrefsDoc {
  if (!(PROJECTION_PREF_KEYS as readonly string[]).includes(key)) return doc;
  const projections = { ...doc.projections };
  if (value === null || !Number.isFinite(value)) delete projections[key];
  else projections[key] = value;
  return bump(doc, { projections }, nowISO);
}

export function withPayrollPhases(doc: PrefsDoc, phases: PayrollPhase[],
                                  nowISO: string): PrefsDoc {
  return bump(doc, { payroll: { phases } }, nowISO);
}

/** module `id` switched on or off for the family: an explicit false stays, so it turns off a module the config
 *  switches on; `null` clears the family's switch, so the module follows the config's seed again. Every other switch
 *  the section holds, one this bundle does not know included, goes with it (within MAX_MODULES, the known ids first) */
export function withModule(doc: PrefsDoc, id: ModuleId, on: boolean | null, nowISO: string): PrefsDoc {
  const switches: Record<string, boolean> = { ...doc.modules };
  if (on === null) delete switches[id];
  else switches[id] = on;
  return bump(doc, { modules: moduleSwitches(switches) }, nowISO);
}

/** the family's AI consent, replaced whole (Settings → AI categorization): passed through the same check a stored
 *  section is read with, so a value the store would refuse is written as off */
export function withAi(doc: PrefsDoc, ai: AiPrefs, nowISO: string): PrefsDoc {
  return bump(doc, { ai: aiPrefs(ai) }, nowISO);
}

// Saved items — generic over the list (SavedKey), so saved reports reuse every op below.

/** the WHOLE prefs doc (every section, as the UTF-8 JSON body the PUT sends) may not exceed this. The
 *  server refuses a body - or the doc it would store once it adds back the lists a PUT omitted - over 64 KB
 *  (MAX_PREFS_BYTES in deploy/scenario_store.py) with a 413, and then EVERY prefs edit (budgets, targets,
 *  payroll) stays in this browser ("refused as too large") until the doc shrinks. 50 entries of 2 KB can exceed 64 KB, so the
 *  count cap and the per-entry cap alone do not keep the doc under it; adding or renaming an item is
 *  refused ("full") when the result would pass this, leaving 8 KB for every other section. */
export const MAX_PREFS_DOC_BYTES = 56 * 1024;
const docBytes = (d: PrefsDoc) => new TextEncoder().encode(JSON.stringify(d)).length;

/** why an add or a rename was refused: "name" (blank or over MAX_SAVED_NAME), "count" (the list is at
 *  MAX_SAVED), "entry" (the item is over MAX_SAVED_ENTRY_CHARS), "full" (the doc would pass
 *  MAX_PREFS_DOC_BYTES), "unknown" (a rename of an id that is not there) */
export type SavedRefusal = "name" | "count" | "entry" | "full" | "unknown";

/** Adds one item to the `key` list, stamping created_at; the new doc, or the reason it was refused
 *  (checked in the order of SavedRefusal). The UI reads the reason; withSaved is the null form. */
export function tryWithSaved<K extends SavedKey>(doc: PrefsDoc, key: K, item: SavedInput<K>, nowISO: string): PrefsDoc | SavedRefusal {
  const list = doc[key] as SavedBase[];
  const name = (item as Omit<SavedBase, "created_at">).name.trim();
  if (!name || name.length > MAX_SAVED_NAME) return "name";
  if (list.length >= MAX_SAVED) return "count";
  const entry = { ...item, name, created_at: nowISO };
  if (JSON.stringify(entry).length > MAX_SAVED_ENTRY_CHARS) return "entry";
  const next = bump(doc, { [key]: [...list, entry] } as Partial<PrefsDoc>, nowISO);
  return docBytes(next) > MAX_PREFS_DOC_BYTES ? "full" : next;
}
export function withSaved<K extends SavedKey>(doc: PrefsDoc, key: K, item: SavedInput<K>, nowISO: string): PrefsDoc | null {
  const r = tryWithSaved(doc, key, item, nowISO);
  return typeof r === "string" ? null : r;
}

/** Deleting is never refused: a full doc can always be made smaller. */
export function withoutSaved(doc: PrefsDoc, key: SavedKey, id: string, nowISO: string): PrefsDoc {
  return bump(doc, { [key]: (doc[key] as SavedBase[]).filter((f) => f.id !== id) } as Partial<PrefsDoc>, nowISO);
}

/** Renames in place (same id, same payload); the new doc, or the reason it was refused. */
export function tryWithRenamedSaved(doc: PrefsDoc, key: SavedKey, id: string, name: string, nowISO: string): PrefsDoc | SavedRefusal {
  const list = doc[key] as SavedBase[];
  const trimmed = name.trim();
  const at = list.findIndex((f) => f.id === id);
  if (at < 0) return "unknown";
  if (!trimmed || trimmed.length > MAX_SAVED_NAME) return "name";
  const entry = { ...list[at], name: trimmed };
  if (JSON.stringify(entry).length > MAX_SAVED_ENTRY_CHARS) return "entry";
  const next = bump(doc, { [key]: list.map((f, i) => (i === at ? entry : f)) } as Partial<PrefsDoc>, nowISO);
  return docBytes(next) > MAX_PREFS_DOC_BYTES ? "full" : next;
}
export function withRenamedSaved(doc: PrefsDoc, key: SavedKey, id: string, name: string, nowISO: string): PrefsDoc | null {
  const r = tryWithRenamedSaved(doc, key, id, name, nowISO);
  return typeof r === "string" ? null : r;
}

// ---------------------------------------------------------------- merging

/** the prefs sections that feed `data.assumptions` (the lists, payroll and the target mix never do: the target mix has no
 *  YAML seed, and its card reads it from the prefs doc) */
export type PrefsOpinions = Pick<PrefsDoc, "budgets" | "targets" | "projections">;

/** Prefs over YAML-seeded assumptions: budgets/targets replace when non-empty
 *  (an explicit in-app map is a complete opinion), projections merge per-key,
 *  only the keys prefs may override (PROJECTION_PREF_KEYS; any other is ignored).
 *  Identity when the prefs doc is empty — zero effect on existing views. */
export function mergeAssumptions(a: Assumptions, prefs: PrefsOpinions): Assumptions {
  const projections = knownProjections(prefs.projections);
  const hasBudgets = Object.keys(prefs.budgets).length > 0;
  const hasTargets = Object.keys(prefs.targets).length > 0;
  const hasProjections = Object.keys(projections).length > 0;
  if (!hasBudgets && !hasTargets && !hasProjections) return a;
  return {
    ...a,
    budgets: hasBudgets ? prefs.budgets : a.budgets,
    targets: hasTargets ? prefs.targets : a.targets,
    projections: hasProjections
      ? { ...a.projections, ...projections }
      : a.projections,
  };
}

/** budgets are keyed by category: a budget on a renamed category follows the rename, unless the
 *  family already set one on the new name (an explicit newer opinion wins). Same instance when
 *  nothing moves. Whole-category entries only — a pair rename never moves a category budget. One pass,
 *  like the ETL's map: a new name that is itself renamed passes its own budget on, so it does not "win"
 *  over the one arriving (the ETL refuses chains and cycles at load, so this only keeps a hand-built
 *  list from dropping a budget). */
export function renameBudgets(budgets: Record<string, number>,
                              renames: readonly CategoryRename[]): Record<string, number> {
  if (!renames.length) return budgets;
  let changed = false;
  const out: Record<string, number> = {};
  for (const [c, v] of Object.entries(budgets)) {
    const to = mapCategoryName(c, renames);
    if (to === c) { out[c] = v; continue; }
    changed = true;
    // an explicit budget on the new name wins - while that name keeps it
    const kept = Object.hasOwn(budgets, to) && mapCategoryName(to, renames) === to;
    if (!kept) out[to] ??= v;
  }
  return changed ? out : budgets;
}

/** What DataContext publishes as `data.assumptions`: the category renames applied to every
 *  category-keyed budget — those pinned in assumptions.yaml AND the family's in-app ones — and then
 *  the usual prefs-over-YAML merge. Identity when nothing moves and the prefs doc is empty. */
export function resolveAssumptions(a: Assumptions, prefs: PrefsOpinions): Assumptions {
  const yamlBudgets = renameBudgets(a.budgets, a.category_renames);
  const prefBudgets = renameBudgets(prefs.budgets, a.category_renames);
  return mergeAssumptions(
    yamlBudgets === a.budgets ? a : { ...a, budgets: yamlBudgets },
    prefBudgets === prefs.budgets ? prefs : { ...prefs, budgets: prefBudgets });
}

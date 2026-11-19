// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// In-app preferences persistence: ./api/prefs (a last-write-wins document, except that the store keeps a
// saved-item list, the target mix, the modules or the AI consent a PUT omits). Document logic lives in lib/prefs.ts.
import { useCallback, useMemo } from "react";
import { uuid4 } from "../lib/ids";
import type { ModuleId } from "../lib/modules";
import type { ReportSpec } from "../lib/reportSpec";
import { parseTargetMix, type TargetMix } from "../lib/targetMix";
import {
  emptyPrefs, KEPT_WHOLE_KEYS, normalizePrefs, PayrollPhase, PrefsDoc, PREFS_LOCAL_KEY, SAVED_KEYS, type SavedInput,
  type SavedKey, type AiPrefs, withAi, withBudget, withModule, withoutSaved, withPayrollPhases, withProjection,
  withRenamedSaved, withSaved, withTarget, withTargetMix,
} from "../lib/prefs";
import { clock } from "../lib/clock";
import type { CategoryRename } from "./types";
import { storeUrl, useSyncedDoc, type SyncedDocOptions } from "./useSyncedDoc";

/** How the prefs doc syncs. Prefs are last-write-wins, but the saved views and reports are family items that
 *  keep growing, so a tab must never write back the lists it loaded over ones another device saved since: a
 *  PUT carries a list only when this tab changed it (`keepOmitted` - the store keeps a list a PUT omits), and
 *  then merged by id with the store's list as it is now (useSyncedDoc mergeById: a deletion elsewhere beats an
 *  edit here). The target mix, the modules and the AI consent ride the same way whole (`keepWhole`, KEPT_WHOLE_KEYS:
 *  each sent only when this tab changed it, the mix `null` to clear), so a budget edit from a tab loaded before another
 *  device saved one of them leaves it alone. The echo hands back what the store kept (`adoptEcho`), and coming back to
 *  the tab re-reads the doc (`resyncOnVisible`). */
export const PREFS_SYNC: SyncedDocOptions<PrefsDoc> = {
  url: storeUrl("prefs"),
  localKey: PREFS_LOCAL_KEY,
  normalize: normalizePrefs,
  empty: emptyPrefs,
  adoptEcho: true,
  keepOmitted: SAVED_KEYS,
  keepWhole: KEPT_WHOLE_KEYS,
  resyncOnVisible: true,
};

export interface PrefsApi {
  loading: boolean;
  localOnly: boolean;
  /** the status of a save the store refused (413: the doc, with the lists it keeps, is over its cap) */
  refused: number | null;
  doc: PrefsDoc;
  /** `renames` = assumptions.category_renames: a budget saved under the category's old name is dropped
   *  with the new one's (lib/prefs.ts withBudget), so clearing a renamed category really clears it */
  setBudget: (category: string, value: number | null, renames?: readonly CategoryRename[]) => void;
  setTarget: (assetClass: string, pct: number | null) => void;
  /** Save the family's target mix (lib/targetMix.ts); false (nothing written) for a mix that is not four buckets from 0
   *  to 100 totalling 100 */
  setTargetMix: (mix: TargetMix) => boolean;
  setProjection: (key: string, value: number | null) => void;
  setPayrollPhases: (phases: PayrollPhase[]) => void;
  /** Switch a module on or off for the family (lib/prefs.ts withModule): it wins over the config's seed either way;
   *  `null` clears the family's switch, and the module follows the config's seed again */
  setModule: (id: ModuleId, on: boolean | null) => void;
  /** Write the family's AI consent whole (lib/prefs.ts withAi): Settings → AI categorization's switch */
  setAi: (ai: AiPrefs) => void;
  /** Save the Transactions tab's current filter query as a named view for the whole family. false
   *  (nothing written) for a blank or over-long name, the 50-view cap, an over-long query or a full
   *  prefs doc (lib/prefs.ts tryWithSaved says which). */
  saveFilter: (name: string, query: string) => boolean;
  removeSavedFilter: (id: string) => void;
  /** false (nothing written) for a blank or over-long name, an unknown id or a full prefs doc */
  renameSavedFilter: (id: string, name: string) => boolean;
  /** Save a Reports-tab spec as a named report for the whole family; false (nothing written) on the same
   *  refusals as saveFilter (name, the 50-report cap, an over-long spec, a full prefs doc) */
  saveReport: (name: string, spec: ReportSpec) => boolean;
  removeReport: (id: string) => void;
  renameReport: (id: string, name: string) => boolean;
}

export function usePrefsStore(): PrefsApi {
  // no saveState here (nothing shows it; see useAnswersStore)
  const { loading, localOnly, refused, doc, commit } = useSyncedDoc<PrefsDoc>(PREFS_SYNC);


  // functional commits: rapid edits (typing in the budget editor) must chain,
  // never clobber each other via a stale doc closure
  const setBudget = useCallback((category: string, value: number | null, renames?: readonly CategoryRename[]) =>
    commit((cur) => withBudget(cur, category, value, clock.nowISO(), renames)), [commit]);
  const setTarget = useCallback((assetClass: string, pct: number | null) =>
    commit((cur) => withTarget(cur, assetClass, pct, clock.nowISO())), [commit]);
  // checked before the commit: a refused mix must not commit (a commit always schedules a PUT)
  const setTargetMix = useCallback((mix: TargetMix): boolean => {
    if (parseTargetMix(mix) === null) return false;
    commit((cur) => withTargetMix(cur, mix, clock.nowISO()) ?? cur);
    return true;
  }, [commit]);
  const setProjection = useCallback((key: string, value: number | null) =>
    commit((cur) => withProjection(cur, key, value, clock.nowISO())), [commit]);
  const setPayrollPhases = useCallback((phases: PayrollPhase[]) =>
    commit((cur) => withPayrollPhases(cur, phases, clock.nowISO())), [commit]);
  const setModule = useCallback((id: ModuleId, on: boolean | null) =>
    commit((cur) => withModule(cur, id, on, clock.nowISO())), [commit]);
  const setAi = useCallback((ai: AiPrefs) => commit((cur) => withAi(cur, ai, clock.nowISO())), [commit]);

  // The saved-item lists share three generic methods (lib/prefs.ts withSaved / withoutSaved /
  // withRenamedSaved, keyed by SavedKey); a kind's public methods below only supply its payload.
  // The pre-check reads the rendered doc and answers false without committing anything; the commit
  // re-runs the op on the CURRENT doc (a stale closure can never overwrite a newer edit or pass the
  // cap) and the boolean returned is the commit's own verdict (commit applies the updater
  // synchronously), so false always means nothing was written.
  const addSaved = useCallback(<K extends SavedKey>(key: K, item: SavedInput<K>): boolean => {
    if (withSaved(doc, key, item, clock.nowISO()) === null) return false;
    let written = false;
    commit((cur) => {
      const next = withSaved(cur, key, item, clock.nowISO());
      written = next !== null;
      return next ?? cur;
    });
    return written;
  }, [commit, doc]);
  const removeSaved = useCallback((key: SavedKey, id: string) =>
    commit((cur) => withoutSaved(cur, key, id, clock.nowISO())), [commit]);
  const renameSaved = useCallback((key: SavedKey, id: string, name: string): boolean => {
    if (withRenamedSaved(doc, key, id, name, clock.nowISO()) === null) return false;
    let written = false;
    commit((cur) => {
      const next = withRenamedSaved(cur, key, id, name, clock.nowISO());
      written = next !== null;
      return next ?? cur;
    });
    return written;
  }, [commit, doc]);

  const saveFilter = useCallback((name: string, query: string) =>
    addSaved("saved_filters", { id: uuid4(), name, query }), [addSaved]);
  const removeSavedFilter = useCallback((id: string) => removeSaved("saved_filters", id), [removeSaved]);
  const renameSavedFilter = useCallback((id: string, name: string) =>
    renameSaved("saved_filters", id, name), [renameSaved]);

  const saveReport = useCallback((name: string, spec: ReportSpec) =>
    addSaved("reports", { id: uuid4(), name, spec }), [addSaved]);
  const removeReport = useCallback((id: string) => removeSaved("reports", id), [removeSaved]);
  const renameReport = useCallback((id: string, name: string) => renameSaved("reports", id, name), [renameSaved]);

  return useMemo(() => ({ loading, localOnly, refused, doc,
           setBudget, setTarget, setTargetMix, setProjection, setPayrollPhases, setModule, setAi,
           saveFilter, removeSavedFilter, renameSavedFilter,
           saveReport, removeReport, renameReport }),
    [loading, localOnly, refused, doc, setBudget, setTarget, setTargetMix, setProjection, setPayrollPhases, setModule, setAi,
     saveFilter, removeSavedFilter, renameSavedFilter, saveReport, removeReport, renameReport]);
}

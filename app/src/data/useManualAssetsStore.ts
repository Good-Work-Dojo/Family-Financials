// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The assets entered in the app: ./api/manual-assets via the family store server (deploy/scenario_store.py), which
// merges every save by asset id, the newer `updated_at` winning per asset, and echoes the merged document, which is
// adopted, so another device's assets arrive with this one's save. The load and a reconnect merge this browser's mirror
// with the store's copy the same way (lib/manualAssets.ts mergeManualAssets), so an edit that never reached the store
// is not lost to a newer document, while an entry only the mirror holds that is older than the store's memory of
// removals is dropped, never sent back. A save goes out at once (an explicit Save, never a keystroke), and the store
// answers a changed document by asking for an ingest without a fetch. Document logic lives in lib/manualAssets.ts.
import { useCallback, useMemo } from "react";
import { clock } from "../lib/clock";
import {
  emptyManualAssets, MANUAL_ASSETS_LOCAL_KEY, type ManualAssetsDoc, mergeManualAssets, normalizeManualAssets,
} from "../lib/manualAssets";
import { type SaveState, storeUrl, useSyncedDoc } from "./useSyncedDoc";

export interface ManualAssetsStoreApi {
  loading: boolean;
  /** the store is unreachable (or refused the last save): what this browser holds is not the family's yet */
  localOnly: boolean;
  /** the status of a save the store refused (400, 413), and its reason in the store's words, until a save gets through */
  refused: number | null;
  refusedReason: string | null;
  /** saves the store has answered 200 / refused since this mounted: a Save watches them move */
  accepted: number;
  refusals: number;
  saveState: SaveState;
  /** this browser's copy: the last one it sent (a refused or unsent one included) or the store's */
  doc: ManualAssetsDoc;
  /** the copy the store last acknowledged (null until it answered): what "saved" means */
  acked: ManualAssetsDoc | null;
  /** send `doc` as the family's document (the card builds it with lib/manualAssets.ts forSave) */
  save: (doc: ManualAssetsDoc) => void;
}

export function useManualAssetsStore(): ManualAssetsStoreApi {
  const { loading, localOnly, refused, refusedReason, accepted, refusals, saveState, doc, acked, commit } =
    useSyncedDoc<ManualAssetsDoc>({
      url: storeUrl("manual-assets"),
      localKey: MANUAL_ASSETS_LOCAL_KEY,
      normalize: normalizeManualAssets,
      empty: emptyManualAssets,
      adoptEcho: true,
      resyncOnVisible: true,
      merge: (local, server) => mergeManualAssets(local, server, clock.now().getTime()),
      debounceMs: 0,
    });
  const save = useCallback((next: ManualAssetsDoc) => commit(next), [commit]);
  return useMemo(() => ({ loading, localOnly, refused, refusedReason, accepted, refusals, saveState, doc, acked, save }),
    [loading, localOnly, refused, refusedReason, accepted, refusals, saveState, doc, acked, save]);
}

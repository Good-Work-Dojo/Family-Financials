// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The hand-entered assets' unsaved draft, held above the routes for the session, so leaving Settings and coming back
// (or opening another page meanwhile) keeps what was typed. The card (sections/settings/ManualAssetsCard.tsx) carries
// it over the stored document each time it mounts (lib/manualAssets.ts rebaseDraft). Nothing here is saved anywhere:
// a reload of the page starts from the family store's copy and this browser's mirror of what it last sent.
import { createContext, type Dispatch, type ReactNode, type SetStateAction, useContext, useMemo, useState } from "react";
import type { ManualAssetsDoc } from "../lib/manualAssets";

/** the draft, the stored copy it was made over, the assets whose fields are open, and those removed on another device
 *  while the draft was edited (each brought back if the draft is saved) */
export interface ManualAssetsDraft {
  draft: ManualAssetsDoc;
  base: ManualAssetsDoc;
  open: readonly string[];
  removedElsewhere: readonly string[];
}

interface DraftApi {
  /** null until the card first made one this session */
  session: ManualAssetsDraft | null;
  setSession: Dispatch<SetStateAction<ManualAssetsDraft | null>>;
}

const DraftCtx = createContext<DraftApi | null>(null);

/** mounted around the shell (App.tsx): one draft for the session */
export function ManualAssetsDraftProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<ManualAssetsDraft | null>(null);
  const value = useMemo(() => ({ session, setSession }), [session]);
  return <DraftCtx.Provider value={value}>{children}</DraftCtx.Provider>;
}

/** the session's draft; outside the provider (a card rendered on its own) the draft lives as long as the card */
export function useManualAssetsDraft(): DraftApi {
  const shared = useContext(DraftCtx);
  const [session, setSession] = useState<ManualAssetsDraft | null>(null);
  return shared ?? { session, setSession };
}

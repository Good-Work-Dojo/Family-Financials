// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Signing out on purpose (Settings → Sign out): the store ends the session, then this browser forgets its copies of
// the family's documents and leaves for the sign-in page.
import { ANSWERS_LOCAL_KEY } from "../lib/answers";
import { IS_DEMO } from "../lib/demo";
import { MANUAL_ASSETS_LOCAL_KEY } from "../lib/manualAssets";
import { PREFS_LOCAL_KEY } from "../lib/prefs";
import { LOCAL_KEY as SCENARIOS_LOCAL_KEY } from "../lib/scenarioStore";
import { end, leave } from "./session";

/** the store's sign-out (deploy/scenario_store.py): a same-origin POST, which the browser sends with its Origin; null in
 *  the demo build, which has no sign-in (Settings shows no button there) */
const LOGOUT_URL: string | null = IS_DEMO ? null : "/auth/logout";

/** the keys of this browser's copies of the family's four documents (each useSyncedDoc caller's `localKey`); the
 *  device's own settings (theme, privacy, essentials) are not the family's and stay */
export const FAMILY_MIRRORS: readonly string[] = [ANSWERS_LOCAL_KEY, PREFS_LOCAL_KEY, MANUAL_ASSETS_LOCAL_KEY,
  SCENARIOS_LOCAL_KEY];

/** Sign this browser out: the store ends the session, then this browser's copies of the family's documents are
 *  forgotten and the page leaves for the sign-in page. false when the store did not say the session ended (a refusal,
 *  no answer): nothing here has changed, and the caller says so. */
export async function signOut(): Promise<boolean> {
  if (LOGOUT_URL === null) return false;
  try {
    const res = await fetch(LOGOUT_URL, { method: "POST" });
    if (!res.ok) return false;
  } catch {
    return false;
  }
  // first: from here no synced document writes its copy again, so a save answering late cannot put one back
  end();
  for (const key of FAMILY_MIRRORS) {
    try { localStorage.removeItem(key); } catch { /* storage unavailable: no copy was kept */ }
  }
  leave.toSignIn();
  return true;
}

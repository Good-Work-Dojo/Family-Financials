// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The sign-in as the page sees it. The store answers 401 to a call made without a live session (it ended, or the
// browser was signed out elsewhere): the first such answer signs the whole page out. Saves stop (useSyncedDoc keeps
// every unsent edit in memory and in this browser's copy and sends nothing), one banner says so with a link to the
// sign-in page (DataContext), and a store call that goes through again signs the page back in, which sends the kept
// edits. Following the link is a full navigation; after signing in, the store sends the browser back to the dashboard,
// whose load finds the kept copies newer than the store's and sends them. The sign-in page itself loads no bundle, so
// nothing here can send a browser round in a loop.
//
// Signing out on purpose is data/signOut.ts, which ends the page's state here (`end`). This module imports nothing of
// the app but the build's demo flag (lib/demo.ts, which imports nothing): useSyncedDoc reads it, and the documents' own
// modules import useSyncedDoc. The demo build has no sign-in: no store call is made there, so the page never signs out.
import { useSyncExternalStore } from "react";
import { IS_DEMO } from "../lib/demo";

/** the sign-in page (deploy/public/login.html, served at this path without a session); the demo build has none and
 *  names the demo's own page instead, so it carries no server path */
export const LOGIN_PATH = IS_DEMO ? "./" : "/login";

/** `in`: no store call has been refused; `out`: the last word from the store was a 401; `ended`: this page signed out
 *  on purpose and is leaving (nothing it does is sent or kept any more) */
export type SessionState = "in" | "out" | "ended";

let state: SessionState = "in";
const listeners = new Set<() => void>();
/** the synced documents that hold edits the store does not have yet, each by its own token */
const unsent = new Set<symbol>();

function become(next: SessionState): void {
  if (state === next) return;
  state = next;
  for (const listener of [...listeners]) listener();
}

export function sessionState(): SessionState {
  return state;
}

/** called by every store call with its answer's status: a 401 signs the page out, a 2xx signs it back in (a page that
 *  signed out on purpose stays out); any other status says nothing about the sign-in */
export function noteStoreStatus(status: number): void {
  if (state === "ended") return;
  if (status === 401) become("out");
  else if (status >= 200 && status < 300) become("in");
}

/** `listener` runs whenever the state changes; the returned function stops it */
export function onSessionChange(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/** whether the page is signed out (a component re-renders when it changes) */
export function useSignedOut(): boolean {
  return useSyncExternalStore(onSessionChange, () => state !== "in", () => false);
}

/** An answer that means "sign in first": a 401, or a redirect that ended on the sign-in page (a proxy in front of the
 *  dashboard that sends page-like requests there). */
export function isSignInAnswer(res: Response): boolean {
  if (res.status === 401) return true;
  if (!res.redirected || !res.url) return false;
  try {
    return new URL(res.url).pathname === LOGIN_PATH;
  } catch {
    return false;
  }
}

/** how the page leaves for the sign-in page: a full navigation that replaces this entry (Back does not return to a page
 *  that cannot load); an object so a test can stand in for the browser */
export const leave = {
  toSignIn(): void {
    if (IS_DEMO) return;            // the demo build has no sign-in to leave for
    location.replace(LOGIN_PATH);
  },
};

/** a synced document says whether it holds edits the store does not have yet (`owner`: its own token) */
export function reportUnsent(owner: symbol, value: boolean): void {
  if (value) unsent.add(owner);
  else unsent.delete(owner);
}

/** some synced document holds edits the store does not have yet: signing out now would lose them */
export function hasUnsentEdits(): boolean {
  return unsent.size > 0;
}

/** the page signed out on purpose: from here nothing is sent, nothing is kept in this browser, and no edit is unsent */
export function end(): void {
  become("ended");
  unsent.clear();
}

/** the state a page starts with (each test starts from it) */
export function resetSession(): void {
  state = "in";
  unsent.clear();
}

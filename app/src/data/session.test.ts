// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ANSWERS_LOCAL_KEY } from "../lib/answers";
import { MANUAL_ASSETS_LOCAL_KEY } from "../lib/manualAssets";
import { PREFS_LOCAL_KEY } from "../lib/prefs";
import { LOCAL_KEY as SCENARIOS_LOCAL_KEY } from "../lib/scenarioStore";
import {
  hasUnsentEdits, isSignInAnswer, leave, LOGIN_PATH, noteStoreStatus, reportUnsent, resetSession, sessionState,
} from "./session";
import { FAMILY_MIRRORS, signOut } from "./signOut";

/** The sign-in as the page sees it: a store call's 401 ends it for the whole page, a call that succeeds restores it,
 *  and signing out on purpose ends it at the store and forgets this browser's copies of the family's documents. */

beforeEach(() => { resetSession(); localStorage.clear(); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("the page's sign-in state", () => {
  it("starts signed in; a store call answered 401 signs the page out; one that succeeds signs it back in", () => {
    expect(sessionState()).toBe("in");
    noteStoreStatus(401);
    expect(sessionState()).toBe("out");
    noteStoreStatus(500);
    noteStoreStatus(404);
    expect(sessionState(), "a failure that is not about the sign-in says nothing of it").toBe("out");
    noteStoreStatus(204);
    expect(sessionState()).toBe("in");
    noteStoreStatus(413);
    expect(sessionState()).toBe("in");
  });

  it("tells a sign-in answer from any other: a 401, or a redirect that ended on the sign-in page", () => {
    const redirected = (res: Response, url: string) => {
      Object.defineProperty(res, "redirected", { value: true });
      Object.defineProperty(res, "url", { value: url });
      return res;
    };
    expect(isSignInAnswer(new Response("{}", { status: 401 }))).toBe(true);
    expect(isSignInAnswer(redirected(new Response("<!doctype html>", { status: 200 }), "http://192.0.2.10:8080/login"))).toBe(true);
    expect(isSignInAnswer(redirected(new Response("<!doctype html>", { status: 200 }), "http://192.0.2.10:8080/login?e=1"))).toBe(true);
    expect(isSignInAnswer(redirected(new Response("<!doctype html>", { status: 200 }), "http://192.0.2.10:8080/portal"))).toBe(false);
    expect(isSignInAnswer(redirected(new Response("<!doctype html>", { status: 200 }), ""))).toBe(false);
    expect(isSignInAnswer(new Response("{}", { status: 200 }))).toBe(false);
    expect(isSignInAnswer(new Response("{}", { status: 403 }))).toBe(false);
  });

  it("the sign-in page is the extensionless /login", () => {
    expect(LOGIN_PATH).toBe("/login");
  });
});

describe("unsent edits", () => {
  it("are what any synced document says it holds, until it says it holds none", () => {
    const a = Symbol("a"), b = Symbol("b");
    expect(hasUnsentEdits()).toBe(false);
    reportUnsent(a, true);
    reportUnsent(b, false);
    expect(hasUnsentEdits()).toBe(true);
    reportUnsent(a, false);
    expect(hasUnsentEdits()).toBe(false);
  });
});

describe("the local mirrors a sign-out forgets", () => {
  it("are the four family documents' copies in this browser", () => {
    expect([...FAMILY_MIRRORS].sort()).toEqual(
      [ANSWERS_LOCAL_KEY, MANUAL_ASSETS_LOCAL_KEY, PREFS_LOCAL_KEY, SCENARIOS_LOCAL_KEY].sort());
  });

  it("are every key a synced document keeps its copy under (a new synced document joins the list)", () => {
    // each hook that syncs a family document names its mirror as `localKey: <CONSTANT>` in app/src/data
    const dir = dirname(fileURLToPath(import.meta.url));
    const named = readdirSync(dir).filter((f) => /\.tsx?$/.test(f) && !/\.test\./.test(f))
      .flatMap((f) => [...readFileSync(join(dir, f), "utf8").matchAll(/\blocalKey:\s*([A-Z][A-Z0-9_]*)/g)].map((m) => m[1]));
    expect(named.sort()).toEqual(["ANSWERS_LOCAL_KEY", "LOCAL_KEY", "MANUAL_ASSETS_LOCAL_KEY", "PREFS_LOCAL_KEY"]);
  });
});

describe("signOut", () => {
  const seedMirrors = () => {
    for (const key of FAMILY_MIRRORS) localStorage.setItem(key, JSON.stringify({ updated_at: "2026-06-01T00:00:00Z" }));
    localStorage.setItem("theme", "light");
    localStorage.setItem("ff.privacy", "1");
  };

  it("posts to the store's sign-out, then forgets the family's documents in this browser and leaves for the sign-in page", async () => {
    seedMirrors();
    const calls: { url: string; method: string }[] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), method: init?.method ?? "GET" });
      return new Response(null, { status: 204 });
    });
    const away = vi.spyOn(leave, "toSignIn").mockImplementation(() => {});
    expect(await signOut()).toBe(true);
    expect(calls).toEqual([{ url: "/auth/logout", method: "POST" }]);
    for (const key of FAMILY_MIRRORS) expect(localStorage.getItem(key), key).toBeNull();
    expect(localStorage.getItem("theme"), "this device's own settings stay").toBe("light");
    expect(localStorage.getItem("ff.privacy")).toBe("1");
    expect(away).toHaveBeenCalledTimes(1);
    expect(sessionState()).toBe("ended");
    noteStoreStatus(200);
    expect(sessionState(), "nothing signs a page that signed out back in").toBe("ended");
  });

  it("a sign-out the store refused or never answered changes nothing here: the copies stay and the page stays", async () => {
    for (const answer of [() => new Response("cross-site", { status: 403 }), () => new Response("{}", { status: 502 }),
      (): Response => { throw new TypeError("Failed to fetch"); }]) {
      resetSession();
      seedMirrors();
      vi.stubGlobal("fetch", async () => answer());
      const away = vi.spyOn(leave, "toSignIn").mockImplementation(() => {});
      expect(await signOut()).toBe(false);
      for (const key of FAMILY_MIRRORS) expect(localStorage.getItem(key), key).not.toBeNull();
      expect(away).not.toHaveBeenCalled();
      expect(sessionState()).toBe("in");
      vi.restoreAllMocks();
    }
  });

  it("forgets the unsent edits it was told of, so nothing asks about them again", async () => {
    reportUnsent(Symbol("x"), true);
    vi.stubGlobal("fetch", async () => new Response(null, { status: 204 }));
    vi.spyOn(leave, "toSignIn").mockImplementation(() => {});
    await signOut();
    expect(hasUnsentEdits()).toBe(false);
  });
});

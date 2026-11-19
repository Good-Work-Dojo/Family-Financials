// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { appDataFactory, snapFactory, txFactory } from "../lib/__fixtures__";
import { DataProvider, useData } from "./DataContext";
import { EXPECTED_SCHEMA_VERSION } from "./types";
import { fetchDeployedBuild, useBuildCheck } from "./useBuildCheck";

// the bundle under test was "built" as build-A
vi.mock("../lib/buildId", () => ({ BUILD_ID: "build-A", VERSION_URL: "./version.json" }));

const base = appDataFactory(snapFactory())();
const calls = vi.hoisted(() => ({ version: [] as { url: string; init?: RequestInit }[] }));

/** serve the exports DataProvider needs, plus version.json as `version` says: a build id, a Response factory, or "throw" */
function serve(version: string | (() => Response) | "throw", schemaVersion = base.manifest.schema_version) {
  const bodies: Record<string, unknown> = {
    "manifest.json": { ...base.manifest, schema_version: schemaVersion },
    "transactions.json": { count: 1, transactions: [txFactory({})({})] },
    "assets_timeseries.json": base.assets,
    "assumptions.json": { ...base.assumptions, category_renames: [], budgets: {} },
    "api/answers": { version: 1, updated_at: "2026-06-09T12:00:00Z", answers: [] },
    "api/prefs": { version: 1, updated_at: "2026-06-09T12:00:00Z", budgets: {}, targets: {}, projections: {}, payroll: { phases: [] } },
  };
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const name = String(url).replace(/^\.\//, "");
    if (name === "version.json") {
      calls.version.push({ url: String(url), init });
      if (version === "throw") throw new TypeError("Failed to fetch");
      return typeof version === "function" ? version() : new Response(JSON.stringify({ build: version }), { status: 200 });
    }
    return name in bodies ? new Response(JSON.stringify(bodies[name]), { status: 200 }) : new Response("nope", { status: 404 });
  });
}

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
}
/** the tab comes back to the foreground */
const comeBack = async () => { await act(async () => { setVisibility("visible"); document.dispatchEvent(new Event("visibilitychange")); }); };
const banner = () => screen.queryByTestId("notice-updated");

function Reloader() {
  const { reload } = useData();
  return <button type="button" onClick={() => void reload()}>reload-now</button>;
}
const mount = async () => {
  render(<DataProvider><Reloader /></DataProvider>);
  await screen.findByRole("button", { name: "reload-now" });
};

beforeEach(() => { calls.version = []; setVisibility("visible"); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

// An app-only deploy bumps no schema, so an open tab ran the old bundle (and its bugs) until someone reloaded by hand.
describe("the dashboard notices a deploy that happened under an open tab", () => {
  it("the same build id shows nothing", async () => {
    serve("build-A");
    await mount();
    await comeBack();
    await waitFor(() => expect(calls.version.length).toBe(1));
    expect(banner()).toBeNull();
  });
  it("a different build id shows the one persistent reload banner - and reloads nothing by itself", async () => {
    serve("build-B");
    await mount();
    await comeBack();
    await waitFor(() => expect(banner()).not.toBeNull());
    expect(banner()!.textContent).toMatch(/The dashboard was updated — reload/);
    expect(screen.getByRole("button", { name: "Reload" })).toBeDefined();
    expect(screen.queryByRole("button", { name: "Dismiss" }), "persistent: no dismiss").toBeNull();
    expect(screen.getByRole("button", { name: "reload-now" }), "the app is still there, edits and all").toBeDefined();
  });
  it("a missing version.json (404: a dev server, an older image) is silent: no banner, no error", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const unhandled = vi.fn();
    process.on("unhandledRejection", unhandled);
    try {
      serve(() => new Response("not found", { status: 404 }));
      await mount();
      await comeBack();
      await waitFor(() => expect(calls.version.length).toBe(1));
      await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
      expect(banner()).toBeNull();
      expect(error).not.toHaveBeenCalled();
      expect(unhandled).not.toHaveBeenCalled();
    } finally { process.off("unhandledRejection", unhandled); }
  });
  it("anything that is not a build id is silent too: an HTML fallback, a gate's 401, a network error, a malformed body", async () => {
    for (const version of [
      () => new Response("<!doctype html><html></html>", { status: 200 }),     // a host that falls back to index.html
      () => new Response("", { status: 401 }),                                  // the passcode gate
      () => new Response("", { status: 503 }),
      () => new Response(JSON.stringify({ build: 5 }), { status: 200 }),
      () => new Response(JSON.stringify({ build: "" }), { status: 200 }),
      () => new Response("null", { status: 200 }),
      "throw" as const,
    ]) {
      calls.version = [];
      serve(version);
      await mount();
      await comeBack();
      await waitFor(() => expect(calls.version.length).toBe(1));
      await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
      expect(banner()).toBeNull();
      cleanup();
    }
  });
  it("asks with no-store, so the answer is the server's and never a cached copy", async () => {
    serve("build-A");
    await mount();
    await comeBack();
    await waitFor(() => expect(calls.version.length).toBe(1));
    expect(calls.version[0].url).toBe("./version.json");
    expect(calls.version[0].init?.cache).toBe("no-store");
  });
  it("checks when the tab becomes visible, never while it is hidden, and not on the page's own first load", async () => {
    serve("build-B");
    await mount();
    await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
    expect(calls.version.length, "no request on the first load").toBe(0);
    await act(async () => { setVisibility("hidden"); document.dispatchEvent(new Event("visibilitychange")); });
    expect(calls.version.length, "a tab going to the background asks nothing").toBe(0);
    await comeBack();
    await waitFor(() => expect(banner()).not.toBeNull());
  });
  it("checks after each data reload (a refresh landed), and shows the banner then", async () => {
    serve("build-B");
    await mount();
    expect(calls.version.length).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "reload-now" }));
    await waitFor(() => expect(banner()).not.toBeNull());
    expect(calls.version.length).toBe(1);
  });
  it("a reload under the same build asks, finds the same id, and shows nothing", async () => {
    serve("build-A");
    await mount();
    fireEvent.click(screen.getByRole("button", { name: "reload-now" }));
    await waitFor(() => expect(calls.version.length).toBe(1));
    await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
    expect(banner()).toBeNull();
  });
  it("is the SAME banner as the newer-schema one: both triggers at once is one notice, not two", async () => {
    serve("build-B", EXPECTED_SCHEMA_VERSION + 1);
    await mount();
    expect(screen.getAllByTestId("notice-updated"), "the schema trigger alone").toHaveLength(1);
    await comeBack();
    await waitFor(() => expect(calls.version.length).toBe(1));
    expect(screen.getAllByTestId("notice-updated"), "and still one with the build trigger as well").toHaveLength(1);
  });
  it("keeps checking after an unchanged answer: a deploy that lands LATER is still noticed", async () => {
    serve("build-A");
    await mount();
    await comeBack();
    await waitFor(() => expect(calls.version.length).toBe(1));
    expect(banner()).toBeNull();
    serve("build-B");                                   // the deploy happens while the tab sits open
    await comeBack();
    await waitFor(() => expect(banner()).not.toBeNull());
  });
  it("stays up once shown (the next check cannot take it back)", async () => {
    serve("build-B");
    await mount();
    await comeBack();
    await waitFor(() => expect(banner()).not.toBeNull());
    serve("build-A");                                   // e.g. a rollback of the deploy: the banner is about THIS bundle's age
    await comeBack();
    expect(banner()).not.toBeNull();
  });
});

describe("useBuildCheck under vite dev / vitest (build id 'dev')", () => {
  function Probe({ current }: { current?: string }) {
    return <output data-testid="stale">{String(useBuildCheck(5, current))}</output>;
  }
  it("never asks and never reports an update, whatever the server says", async () => {
    serve("build-B");
    render(<Probe current="dev" />);
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); await new Promise((r) => setTimeout(r, 20)); });
    expect(calls.version.length).toBe(0);
    expect(screen.getByTestId("stale").textContent).toBe("false");
  });
  it("with a real build id, a reload count above zero checks at once", async () => {
    serve("build-B");
    render(<Probe current="build-A" />);
    await waitFor(() => expect(screen.getByTestId("stale").textContent).toBe("true"));
  });
});

describe("fetchDeployedBuild", () => {
  it("returns the served id, or null for anything it cannot read", async () => {
    serve("build-Z");
    expect(await fetchDeployedBuild()).toBe("build-Z");
    serve(() => new Response("nope", { status: 404 }));
    expect(await fetchDeployedBuild()).toBeNull();
    serve("throw");
    expect(await fetchDeployedBuild()).toBeNull();
  });
});

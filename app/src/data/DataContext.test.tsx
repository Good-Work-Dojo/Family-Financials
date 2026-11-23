// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { matchPreset, presetRange, SPENDING_PRESETS } from "../filters/dateRange";
import { EMPTY_FILTERS } from "../filters/filters";
import { isAutoWindow, rollingPreset, savedFilterQuery } from "../filters/useSpendingDefaults";
import { appDataFactory, snapFactory, txFactory } from "../lib/__fixtures__";
import { buildIndexes, DataProvider, type LookOutcome, NOTHING_BUILT_RECHECK_MS, useData } from "./DataContext";
import { leave, noteStoreStatus, resetSession } from "./session";
import { type AppData, type CategoryRename, EXPECTED_SCHEMA_VERSION, MIN_SCHEMA_VERSION, type Transaction } from "./types";
import type { AnswersApi } from "./useAnswersStore";

/** The wiring behind category renames: DataProvider sees the stored answers and the
 *  category-keyed budgets through assumptions.category_renames, the same map the ETL applied. The
 *  pure pieces are pinned in lib/{categoryRenames,answers,prefs}.test.ts; this pins that the
 *  provider actually calls them (the memos are where a dropped call would hide). */

const RENAMES: CategoryRename[] = [{ from: { category: "Eating Out" }, to: { category: "Dining" } }];
const base = appDataFactory(snapFactory())();
// a row the ETL has ALREADY renamed (its export says Dining), and an answer stored under the old name
const ROW = txFactory({ category: "Dining", subcategory: "Coffee", needs_review: false })({});
const ANSWERED_BEFORE_INGEST = "2026-06-09T12:00:00Z";            // manifest.generated_at is 2026-06-10
const ANSWERS = { version: 1, updated_at: "2026-06-09T12:00:00Z", answers: [{
  key: `txn:${ROW.id}`, scope: "txn", txn_id: ROW.id,
  set: { category: "Eating Out", subcategory: "Coffee", needs_review: false },
  answered_at: ANSWERED_BEFORE_INGEST }] };

function serve(opts: { yamlBudgets?: Record<string, number>; prefsBudgets?: Record<string, number>;
                       renames?: CategoryRename[]; rows?: Transaction[]; answers?: unknown;
                       /** the answers GET waits for this */
                       holdAnswers?: Promise<void>; schemaVersion?: number }) {
  const assumptions: Record<string, unknown> = { ...base.assumptions, category_renames: opts.renames ?? RENAMES,
                                                 budgets: opts.yamlBudgets ?? {} };
  const bodies: Record<string, unknown> = {
    "manifest.json": { ...base.manifest, schema_version: opts.schemaVersion ?? base.manifest.schema_version },
    "transactions.json": { count: 1, transactions: opts.rows ?? [ROW] },
    "assets_timeseries.json": base.assets,
    "assumptions.json": assumptions,
    "api/answers": opts.answers ?? ANSWERS,
    "api/prefs": { version: 1, updated_at: "2026-06-09T12:00:00Z", budgets: opts.prefsBudgets ?? {},
                   targets: {}, projections: {}, payroll: { phases: [] } },
  };
  vi.stubGlobal("fetch", async (url: string) => {
    const name = String(url).replace(/^\.\//, "");
    if (name === "api/answers" && opts.holdAnswers) await opts.holdAnswers;
    return name in bodies ? new Response(JSON.stringify(bodies[name]), { status: 200 })
                          : new Response("nope", { status: 404 });   // optional exports degrade
  });
  return bodies;                                                       // a test may take a file away later
}

function Probe() {
  const { data, indexes, overlay, answers } = useData();
  return <pre data-testid="probe">{JSON.stringify({
    answersLoaded: !answers.loading, answers: answers.doc.answers.length,
    budgets: data.assumptions.budgets,
    pending: overlay.pendingIds.size, conflicted: overlay.conflictedKeys.size,
    rowCategory: data.transactions[0].category,
    purchases: data.purchases.map((t) => [t.id, t.category]),
    parts: data.transactions.map((t) => [t.id, t.category]),
    byVendor: [...indexes.byVendor].map(([v, rows]) => [v, rows.map((t) => [t.id, t.category])]),
  })}</pre>;
}
const read = () => JSON.parse(screen.getByTestId("probe").textContent ?? "null");
async function probed() {
  render(<DataProvider><Probe /></DataProvider>);
  await screen.findByTestId("probe");
  await waitFor(() => expect(read().answersLoaded).toBe(true));
  return read();
}

beforeEach(() => { localStorage.clear(); resetSession(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("DataProvider and category renames", () => {
  it("sees an answer stored under the old name as absorbed, not conflicted or pending", async () => {
    serve({});
    // answers: 1 proves the stored answer really was loaded, so zero conflicts is not vacuous
    expect(await probed()).toMatchObject({ answers: 1, pending: 0, conflicted: 0, rowCategory: "Dining" });
  });

  it("without the rename the same answer reads as conflicted (the test above can fail)", async () => {
    serve({ renames: [] });
    expect(await probed()).toMatchObject({ answers: 1, pending: 0, conflicted: 1 });
  });

  it("renames the budgets pinned in assumptions.yaml", async () => {
    serve({ yamlBudgets: { "Eating Out": 800, Groceries: 1200 } });
    expect((await probed()).budgets).toEqual({ Dining: 800, Groceries: 1200 });
  });

  it("renames the family's in-app budgets, which replace the YAML ones as before", async () => {
    serve({ yamlBudgets: { "Eating Out": 700, Utilities: 300 }, prefsBudgets: { "Eating Out": 900, Groceries: 1200 } });
    expect((await probed()).budgets).toEqual({ Dining: 900, Groceries: 1200 });
  });
});

describe("DataProvider keeps the vendor view in step with the answers overlay", () => {
  const tx = txFactory({ vendor: "Synth Mart", category: "Dining", needs_review: false });
  const parent = tx({ id: "p", is_parent: true, amount: -300, category: "Groceries" });
  const partA = tx({ id: "ca", parent_id: "p", amount: -210, category: "Groceries", category_source: "split" });
  const partB = tx({ id: "cb", parent_id: "p", amount: -90, category: "Shopping", category_source: "split" });
  const ordinary = tx({ id: "o", vendor: "Other", amount: -40 });
  const rows = [parent, partA, partB, ordinary];
  const answered = (a: object) => ({ answered_at: "2026-06-11T12:00:00Z", ...a });   // after the ingest (2026-06-10): pending
  const doc = (...answers: object[]) => ({ version: 1, updated_at: "2026-06-11T12:00:00Z", answers: answers.map(answered) });

  it("a repainted ordinary row is repainted in the vendor view and its byVendor list; a parent and parts stay as exported", async () => {
    serve({ renames: [], rows, answers: doc(
      { key: "txn:o", scope: "txn", txn_id: "o", set: { category: "Pets" } },
      { key: "vendor:Synth Mart", scope: "vendor", vendor: "Synth Mart", set: { category: "Pets" } },    // never reaches the parts
    ) });
    const p = await probed();
    expect(p.pending).toBe(1);                                            // the ordinary row only: no parent in the overlay input
    expect(p.parts).toEqual([["ca", "Groceries"], ["cb", "Shopping"], ["o", "Pets"]]);           // the category view: parts + ordinary
    expect(p.purchases).toEqual([["p", "Groceries"], ["o", "Pets"]]);                             // the vendor view: parent + ordinary, repainted
    expect(p.byVendor).toEqual([["Synth Mart", [["p", "Groceries"]]], ["Other", [["o", "Pets"]]]]);
  });

  it("with nothing pending the vendor view is the export's own", async () => {
    serve({ renames: [], rows, answers: doc() });
    const p = await probed();
    expect(p.pending).toBe(0);
    expect(p.purchases).toEqual([["p", "Groceries"], ["o", "Dining"]]);
    expect(p.byVendor).toEqual([["Synth Mart", [["p", "Groceries"]]], ["Other", [["o", "Dining"]]]]);
  });

  it("with nothing pending the provider keeps handing out the loaded data object (the memo's identity early-return)", async () => {
    serve({ renames: [], rows, answers: doc() });
    const seen: AppData[] = [];
    let api = null as AnswersApi | null;
    function Spy() {
      const { data, answers } = useData();
      seen.push(data);
      api = answers;
      return <i data-testid="spy" />;
    }
    render(<DataProvider><Spy /></DataProvider>);
    await screen.findByTestId("spy");
    await waitFor(() => expect(api!.loading).toBe(false));
    const before = seen.length;
    // an answer for a row that is not in the export changes the answers doc (the overlay memo re-runs) but nothing on screen
    act(() => api!.answerTxn("no-such-row", { category: "Pets" }));
    await waitFor(() => expect(seen.length).toBeGreaterThan(before));
    expect(new Set(seen).size, "one data object, not a fresh copy per recomputation").toBe(1);
    expect(seen[0].purchases).not.toBe(seen[0].transactions);        // a parent exists: two arrays, both as loaded
    expect(seen[0].transactions.map((t) => t.id)).toEqual(["ca", "cb", "o"]);
    expect(seen[0].purchases.map((t) => t.id)).toEqual(["p", "o"]);
  });
});

describe("DataProvider waits for the answers doc", () => {
  it("renders nothing until the answers resolve, so the first frame already carries the overlay", async () => {
    let release!: () => void;
    serve({ renames: [], holdAnswers: new Promise<void>((r) => { release = r; }) });     // the stored answer reads conflicted
    const seen: boolean[] = [];
    function Spy() {
      const { answers, overlay } = useData();
      seen.push(answers.loading);
      return <i data-testid="spy">{overlay.conflictedKeys.size}</i>;
    }
    render(<DataProvider><Spy /></DataProvider>);
    // the exports and the prefs doc have every chance to resolve while the answers are held
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    expect(screen.queryByTestId("spy"), "nothing rendered while the answers load").toBeNull();
    await act(async () => { release(); });
    await screen.findByTestId("spy");
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.every((l) => !l), "no frame rendered without the answers").toBe(true);
    expect(screen.getByTestId("spy").textContent, "the stored answer's overlay is there on the first frame").toBe("1");
  });
});

describe("DataProvider and an export set from a NEWER schema (the dashboard was redeployed under an open tab)", () => {
  const shown = async (schemaVersion: number) => {
    serve({ schemaVersion });
    render(<DataProvider><i data-testid="app" /></DataProvider>);
    await screen.findByTestId("app");
    return screen.queryByTestId("notice-updated");
  };
  it("a newer schema_version shows the persistent reload banner, over the working app", async () => {
    const notice = await shown(EXPECTED_SCHEMA_VERSION + 1);
    expect(notice?.textContent).toMatch(/The dashboard was updated — reload/);
    expect(screen.getByRole("button", { name: "Reload" })).toBeDefined();
    expect(screen.queryByRole("button", { name: "Dismiss" }), "persistent: no dismiss").toBeNull();
  });
  it("the version this bundle expects shows nothing, and neither does the oldest it reads", async () => {
    expect(await shown(EXPECTED_SCHEMA_VERSION)).toBeNull();
    cleanup();
    expect(await shown(MIN_SCHEMA_VERSION)).toBeNull();
  });
});

// An export set older than the oldest this bundle reads (an old image's refresher wrote it during a deploy) is not read:
// the same reload notice, saying the data is rebuilt at the next refresh, never the "couldn't load" screen
describe("DataProvider and an export set OLDER than the bundle reads", () => {
  function Reloader() {
    const { reload } = useData();
    return <button type="button" onClick={() => void reload()}>reload-now</button>;
  }
  it("a first load: the reload notice alone, and no app", async () => {
    serve({ schemaVersion: MIN_SCHEMA_VERSION - 1 });
    render(<DataProvider><i data-testid="app" /></DataProvider>);
    const notice = await screen.findByTestId("notice-updated");
    expect(notice.textContent).toMatch(/^The dashboard was updated — its data is rebuilt at the next refresh; reload this page then\./);
    expect(screen.getByRole("button", { name: "Reload" })).toBeDefined();
    expect(screen.queryByTestId("app")).toBeNull();
    expect(screen.queryByRole("alert"), "not the couldn't-load screen").toBeNull();
  });
  it("a reload that meets one keeps the app as it was, under the same notice", async () => {
    const bodies = serve({});
    render(<DataProvider><Reloader /><i data-testid="app" /></DataProvider>);
    await screen.findByTestId("app");
    expect(screen.queryByTestId("notice-updated")).toBeNull();
    bodies["manifest.json"] = { ...(bodies["manifest.json"] as object), schema_version: MIN_SCHEMA_VERSION - 1 };
    fireEvent.click(screen.getByRole("button", { name: "reload-now" }));
    expect((await screen.findByTestId("notice-updated")).textContent).toMatch(/its data is rebuilt at the next refresh/);
    expect(screen.getByTestId("app")).toBeDefined();
    expect(screen.queryByTestId("notice-reload-failed")).toBeNull();
  });
});

describe("DataProvider when a reload fails", () => {
  function Reloader() {
    const { reload } = useData();
    return <button type="button" onClick={() => void reload()}>reload-now</button>;
  }
  it("keeps the working app and offers a retry (and a dismiss); a retry that works clears the banner", async () => {
    const bodies = serve({});
    render(<DataProvider><Reloader /></DataProvider>);
    await screen.findByRole("button", { name: "reload-now" });
    const manifest = bodies["manifest.json"];
    delete bodies["manifest.json"];                                    // e.g. a 502 while web restarts
    fireEvent.click(screen.getByRole("button", { name: "reload-now" }));
    const notice = await screen.findByTestId("notice-reload-failed");
    expect(notice.textContent).toMatch(/Couldn't load the latest data — still showing what was loaded before/);
    expect(screen.getByRole("button", { name: "reload-now" }), "the app is still there").toBeDefined();
    expect(screen.queryByText("Couldn't load data"), "not the first-load screen").toBeNull();
    bodies["manifest.json"] = manifest;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.queryByTestId("notice-reload-failed")).toBeNull());
  });
  it("the banner can be dismissed", async () => {
    const bodies = serve({});
    render(<DataProvider><Reloader /></DataProvider>);
    await screen.findByRole("button", { name: "reload-now" });
    delete bodies["manifest.json"];
    fireEvent.click(screen.getByRole("button", { name: "reload-now" }));
    await screen.findByTestId("notice-reload-failed");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByTestId("notice-reload-failed")).toBeNull();
    expect(screen.getByRole("button", { name: "reload-now" })).toBeDefined();
  });
  it("a FIRST load that fails is still the full-screen error, in owner-facing words", async () => {
    const bodies = serve({});
    delete bodies["manifest.json"];
    render(<DataProvider><Reloader /></DataProvider>);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/Couldn't load data/);
    expect(alert.textContent).toMatch(/this clears up within a minute or two/);
    expect(alert.textContent, "no developer instructions").not.toMatch(/make ingest|finance-app/);
    expect(screen.getByRole("button", { name: "Try again" })).toBeDefined();
    expect(screen.queryByRole("button", { name: "reload-now" })).toBeNull();
  });
});

describe("DataProvider and the sign-in", () => {
  function Reloader() {
    const { reload } = useData();
    return <button type="button" onClick={() => void reload()}>reload-now</button>;
  }
  afterEach(() => vi.restoreAllMocks());

  it("a store call answered 401 shows ONE banner with a full link to the sign-in page, over the app that stays", async () => {
    serve({});
    render(<DataProvider><i data-testid="app" /></DataProvider>);
    await screen.findByTestId("app");
    expect(screen.queryByTestId("notice-signed-out")).toBeNull();
    act(() => { noteStoreStatus(401); noteStoreStatus(401); });          // two documents' saves, one sign-in
    const notices = screen.getAllByTestId("notice-signed-out");
    expect(notices).toHaveLength(1);
    expect(notices[0].textContent).toBe("Signed out — sign in to save your changes.Sign in");
    const link = screen.getByRole("link", { name: "Sign in" });
    expect(link.getAttribute("href"), "a plain link: the browser leaves the page, the sign-in page loads no bundle").toBe("/login");
    expect(screen.getByTestId("app"), "the page and its unsent edits stay until the person follows the link").toBeDefined();
    act(() => noteStoreStatus(200));
    expect(screen.queryByTestId("notice-signed-out"), "a call that went through: signed in again").toBeNull();
  });

  it("an export read answered 401 sends the browser to the sign-in page", async () => {
    const away = vi.spyOn(leave, "toSignIn").mockImplementation(() => {});
    const bodies = serve({});
    render(<DataProvider><Reloader /></DataProvider>);
    await screen.findByRole("button", { name: "reload-now" });
    expect(away).not.toHaveBeenCalled();
    const base = globalThis.fetch;
    vi.stubGlobal("fetch", async (url: string) =>
      (String(url).endsWith(".json") ? new Response('{"error": "signed out"}', { status: 401 }) : base(url)));
    fireEvent.click(screen.getByRole("button", { name: "reload-now" }));
    await waitFor(() => expect(away).toHaveBeenCalled());
    expect(bodies["manifest.json"]).toBeDefined();
  });

  it("an export read that a proxy redirected to the sign-in page sends the browser there too", async () => {
    const away = vi.spyOn(leave, "toSignIn").mockImplementation(() => {});
    vi.stubGlobal("fetch", async () => {
      const res = new Response("<!doctype html><title>Family Financials</title>", { status: 200 });
      Object.defineProperty(res, "redirected", { value: true });
      Object.defineProperty(res, "url", { value: "http://192.0.2.10:8080/login" });
      return res;
    });
    render(<DataProvider><i data-testid="app" /></DataProvider>);
    await waitFor(() => expect(away).toHaveBeenCalled());
  });
});

// No manifest — a 404, or a page where the file should be — is no failure: nothing has been built yet. The app's
// nothing-built view shows instead of the error screen, and the provider looks again until the exports exist.
describe("DataProvider when nothing is built yet", () => {
  const unbuilt = (checkAgain: () => void) => <button type="button" onClick={checkAgain}>nothing-built</button>;
  /** the exports with manifest.json answered by `answer` (a 404 when it is absent) */
  function serveManifest(answer?: () => Response) {
    const bodies = serve({});
    const manifest = bodies["manifest.json"];
    delete bodies["manifest.json"];
    const base = globalThis.fetch;
    vi.stubGlobal("fetch", async (url: string) =>
      (String(url).replace(/^\.\//, "") === "manifest.json" && answer ? answer() : base(url)));
    return { bodies, manifest };
  }

  it("a manifest the server does not have (404): the nothing-built view, not the error screen", async () => {
    serveManifest();
    render(<DataProvider nothingBuilt={unbuilt}><Probe /></DataProvider>);
    await screen.findByRole("button", { name: "nothing-built" });
    expect(screen.queryByRole("alert"), "no error screen").toBeNull();
    expect(screen.queryByTestId("probe")).toBeNull();
  });

  it("a page where the manifest should be (a static server's fallback): the nothing-built view too", async () => {
    serveManifest(() => new Response("<!doctype html><html><body>app</body></html>", { status: 200 }));
    render(<DataProvider nothingBuilt={unbuilt}><Probe /></DataProvider>);
    await screen.findByRole("button", { name: "nothing-built" });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a 500, a manifest that does not parse and a network error keep the error screen", async () => {
    for (const answer of [() => new Response("{}", { status: 500 }), () => new Response("{\"schema_version\": 20,", { status: 200 }),
      () => { throw new TypeError("Failed to fetch"); }]) {
      serveManifest(answer);
      render(<DataProvider nothingBuilt={unbuilt}><Probe /></DataProvider>);
      const alert = await screen.findByRole("alert");
      expect(alert.textContent).toMatch(/Couldn't load data/);
      expect(screen.queryByRole("button", { name: "nothing-built" })).toBeNull();
      cleanup();
    }
  });

  it("a redirect that ends on a page or a 404 (a login or portal page in front of the dashboard) is the error screen", async () => {
    const redirected = (res: Response) => Object.defineProperty(res, "redirected", { value: true });
    for (const answer of [() => redirected(new Response("<!doctype html><html><body>Sign in</body></html>", { status: 200 })),
      () => redirected(new Response("Not Found", { status: 404 }))]) {
      serveManifest(answer);
      render(<DataProvider nothingBuilt={unbuilt}><Probe /></DataProvider>);
      const alert = await screen.findByRole("alert");
      expect(alert.textContent).toMatch(/Couldn't load data/);
      expect(screen.queryByRole("button", { name: "nothing-built" }), "never told that no ingest has run").toBeNull();
      cleanup();
    }
  });

  it("while the view is on screen a look that fails for another reason leaves it there, still looking again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let answer: (() => Response) | undefined;
      const bodies = serve({});
      const manifest = bodies["manifest.json"];
      delete bodies["manifest.json"];
      const base = globalThis.fetch;
      vi.stubGlobal("fetch", async (url: string) =>
        (String(url).replace(/^\.\//, "") === "manifest.json" && answer ? answer() : base(url)));
      const checks: Promise<LookOutcome>[] = [];
      const view = (checkAgain: () => Promise<LookOutcome>) => (
        <button type="button" onClick={() => { checks.push(checkAgain()); }}>nothing-built</button>);
      render(<DataProvider nothingBuilt={view}><Probe /></DataProvider>);
      await screen.findByRole("button", { name: "nothing-built" });
      // a network blip, then a 500: neither leaves the view, and each look says it failed, never "still nothing built"
      answer = () => { throw new TypeError("Failed to fetch"); };
      await act(async () => { await vi.advanceTimersByTimeAsync(NOTHING_BUILT_RECHECK_MS); });
      expect(screen.getByRole("button", { name: "nothing-built" })).toBeDefined();
      answer = () => new Response("{}", { status: 500 });
      fireEvent.click(screen.getByRole("button", { name: "nothing-built" }));
      expect(await checks[0]).toBe("failed");
      expect(screen.queryByRole("alert"), "no error screen").toBeNull();
      expect(screen.getByRole("button", { name: "nothing-built" })).toBeDefined();
      // still nothing built: the look says so
      answer = undefined;
      fireEvent.click(screen.getByRole("button", { name: "nothing-built" }));
      expect(await checks[1]).toBe("nothing-built");
      // and the timer is still running: once the exports exist the app opens by itself
      bodies["manifest.json"] = manifest;
      await act(async () => { await vi.advanceTimersByTimeAsync(NOTHING_BUILT_RECHECK_MS); });
      await screen.findByTestId("probe");
    } finally { vi.useRealTimers(); }
  });

  it("the view is told how many timed looks found nothing built; a timed look that failed is not counted", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let answer: (() => Response) | undefined;
      serve({});
      const base = globalThis.fetch;
      vi.stubGlobal("fetch", async (url: string) => {
        if (String(url).replace(/^\.\//, "") !== "manifest.json") return base(url);
        return answer ? answer() : new Response("Not Found", { status: 404 });
      });
      const view = (_: () => Promise<LookOutcome>, quietLooks: number) => <p data-testid="quiet">{quietLooks}</p>;
      render(<DataProvider nothingBuilt={view}><Probe /></DataProvider>);
      expect((await screen.findByTestId("quiet")).textContent).toBe("0");
      await act(async () => { await vi.advanceTimersByTimeAsync(NOTHING_BUILT_RECHECK_MS); });
      expect(screen.getByTestId("quiet").textContent).toBe("1");
      answer = () => { throw new TypeError("Failed to fetch"); };
      await act(async () => { await vi.advanceTimersByTimeAsync(NOTHING_BUILT_RECHECK_MS); });
      expect(screen.getByTestId("quiet").textContent, "a failed timed look counts nothing").toBe("1");
      answer = undefined;
      await act(async () => { await vi.advanceTimersByTimeAsync(NOTHING_BUILT_RECHECK_MS); });
      expect(screen.getByTestId("quiet").textContent).toBe("2");
    } finally { vi.useRealTimers(); }
  });

  it("a look that finds the exports says it loaded them", async () => {
    const { bodies, manifest } = serveManifest();
    const checks: Promise<LookOutcome>[] = [];
    const view = (checkAgain: () => Promise<LookOutcome>) => (
      <button type="button" onClick={() => { checks.push(checkAgain()); }}>nothing-built</button>);
    render(<DataProvider nothingBuilt={view}><Probe /></DataProvider>);
    await screen.findByRole("button", { name: "nothing-built" });
    bodies["manifest.json"] = manifest;
    fireEvent.click(screen.getByRole("button", { name: "nothing-built" }));
    expect(await checks[0]).toBe("loaded");
    await screen.findByTestId("probe");
  });

  it("looking again once the exports exist replaces the view with the app", async () => {
    const { bodies, manifest } = serveManifest();
    render(<DataProvider nothingBuilt={unbuilt}><Probe /></DataProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "nothing-built" }));
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    expect(screen.queryByTestId("probe"), "still nothing built").toBeNull();
    bodies["manifest.json"] = manifest;              // the first ingest has written its exports
    fireEvent.click(screen.getByRole("button", { name: "nothing-built" }));
    await screen.findByTestId("probe");
    expect(screen.queryByRole("button", { name: "nothing-built" })).toBeNull();
  });

  /** how many times manifest.json has been asked for since the stub went in */
  const countManifestReads = () => {
    const seen = { n: 0 };
    const base = globalThis.fetch;
    vi.stubGlobal("fetch", async (url: string) => {
      if (String(url).replace(/^\.\//, "") === "manifest.json") seen.n += 1;
      return base(url);
    });
    return seen;
  };

  it("looks again by itself every NOTHING_BUILT_RECHECK_MS while the view is on screen", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { bodies, manifest } = serveManifest();
      render(<DataProvider nothingBuilt={unbuilt}><Probe /></DataProvider>);
      await screen.findByRole("button", { name: "nothing-built" });
      bodies["manifest.json"] = manifest;
      await act(async () => { await vi.advanceTimersByTimeAsync(NOTHING_BUILT_RECHECK_MS - 100); });
      expect(screen.queryByTestId("probe"), "not before its time").toBeNull();
      await act(async () => { await vi.advanceTimersByTimeAsync(100); });
      await screen.findByTestId("probe");
    } finally { vi.useRealTimers(); }
  });

  it("stops looking once the view is gone", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      serveManifest();
      const reads = countManifestReads();
      const view = render(<DataProvider nothingBuilt={unbuilt}><Probe /></DataProvider>);
      await screen.findByRole("button", { name: "nothing-built" });
      await act(async () => { await vi.advanceTimersByTimeAsync(NOTHING_BUILT_RECHECK_MS); });
      const whileShown = reads.n;
      expect(whileShown, "the first load and one re-check").toBe(2);
      view.unmount();
      await vi.advanceTimersByTimeAsync(3 * NOTHING_BUILT_RECHECK_MS);
      expect(reads.n, "no re-check after the unmount").toBe(whileShown);
    } finally { vi.useRealTimers(); }
  });

  it("without a nothing-built view, a missing manifest is the error screen", async () => {
    serveManifest();
    render(<DataProvider><Probe /></DataProvider>);
    expect((await screen.findByRole("alert")).textContent).toMatch(/manifest\.json: HTTP 404/);
  });
});

describe("DataProvider re-renders its consumers only when something they read changed", () => {
  /** the store's echo: answers echo what was sent, prefs too (nothing else is stored meanwhile) */
  function serveEchoing(opts: Parameters<typeof serve>[0]) {
    const bodies = serve(opts);
    const get = globalThis.fetch;
    const puts: string[] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (init?.method === "PUT") { puts.push(String(url)); return new Response(String(init.body), { status: 200 }); }
      return get(url);
    });
    return { bodies, puts };
  }
  it("a saved answer re-renders a consumer for the new doc, never for the save's saving → saved → idle ticks", async () => {
    const { puts } = serveEchoing({ renames: [] });
    let renders = 0;
    let api = null as AnswersApi | null;
    function Spy() { renders += 1; api = useData().answers; return <i data-testid="spy" />; }
    render(<DataProvider><Spy /></DataProvider>);
    await screen.findByTestId("spy");
    act(() => api!.answerTxn("no-such-row", { category: "Pets" }));
    const afterCommit = renders;
    await waitFor(() => expect(puts).toEqual(["./api/answers"]), { timeout: 3000 });
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });   // the PUT answered: "saved"
    expect(renders, "the saved tick and the equal echo reach no consumer").toBe(afterCommit);
  });
  it("saving a view changes prefs but not the merged data a page reads", async () => {
    serveEchoing({ renames: [], prefsBudgets: { Dining: 400 } });           // budgets in prefs: data is a merged copy
    const seen: AppData[] = [];
    let prefs = null as ReturnType<typeof useData>["prefs"] | null;
    function Spy() { const ctx = useData(); seen.push(ctx.data); prefs = ctx.prefs; return <i data-testid="spy" />; }
    render(<DataProvider><Spy /></DataProvider>);
    await screen.findByTestId("spy");
    const before = seen.length;
    act(() => { prefs!.saveFilter("Synthetic view", "q=coffee"); });
    expect(prefs!.doc.saved_filters).toHaveLength(1);
    expect(seen.length).toBeGreaterThan(before);
    expect(new Set(seen).size, "one data object throughout").toBe(1);
    expect(seen[0].assumptions.budgets).toEqual({ Dining: 400 });
  });
});

describe("buildIndexes", () => {
  it("tagList is every tag any transaction carries: sorted, unique, empty when nothing is tagged", () => {
    const tx = txFactory();
    expect(buildIndexes([tx({ tags: ["kids", "home"] }), tx({ tags: ["kids", "trip"] }), tx({ tags: [] }), tx({ tags: ["Zed"] })]).tagList)
      .toEqual(["Zed", "home", "kids", "trip"]);                       // plain sort(): the ETL's tag order
    expect(buildIndexes([tx({ tags: [] })]).tagList).toEqual([]);
    expect(buildIndexes([]).tagList).toEqual([]);
  });
  it("byVendor lists purchases (the vendor view), the category and tag lists read the parts (the category view)", () => {
    const tx = txFactory({ vendor: "Synth Mart" });
    const parent = tx({ id: "p", is_parent: true, category: "Groceries", tags: ["a"] });
    // "z" sits on one part only (a tags-only answer can tag a single part): the tag list must still offer it
    const parts = [tx({ id: "c1", parent_id: "p", category: "Groceries", tags: ["a"] }), tx({ id: "c2", parent_id: "p", category: "Shopping", tags: ["a", "z"] })];
    const other = tx({ id: "o", vendor: "Other", category: "Pets", tags: ["b"] });
    const ix = buildIndexes([...parts, other], [parent, other]);
    expect(ix.byVendor.get("Synth Mart")!.map((t) => t.id)).toEqual(["p"]);
    expect(ix.categoryList).toEqual(["Groceries", "Pets", "Shopping"]);         // Shopping exists only on a part
    expect(ix.tagList).toEqual(["a", "b", "z"]);                                // z exists only on a part
    expect(ix.childrenByParent.get("p")!.map((t) => t.id)).toEqual(["c1", "c2"]);
  });
  it("without a second argument the vendor view is the category view (no splits)", () => {
    const tx = txFactory();
    const rows = [tx({}), tx({})];
    expect(buildIndexes(rows).byVendor.get("V")).toHaveLength(2);
  });
  it("dataRange is the days of the oldest and newest row, in any order, a future-dated row included", () => {
    const tx = txFactory();
    const rows = [tx({ date: "2026-03-14" }), tx({ date: "2025-11-02" }), tx({ date: "2026-07-21" }), tx({ date: "2026-01-09" })];
    expect(buildIndexes(rows).dataRange).toEqual({ first: "2025-11-02", last: "2026-07-21" });
    expect(buildIndexes([tx({ date: "2026-03-14" })]).dataRange, "one row is both ends").toEqual({ first: "2026-03-14", last: "2026-03-14" });
  });
  it("with no transaction there is no date range and no day the data reaches (null), and no other key says there is one", () => {
    const ix = buildIndexes([], [], "2026-10-31T18:00:00-04:00");
    expect(ix.dataRange).toBeNull();
    expect(ix.dataThrough).toBeNull();
    expect(Object.keys(ix).sort()).toEqual(["byVendor", "categoryList", "childrenByParent", "dataRange", "dataThrough", "tagList", "vendorList"]);
  });
});

// One day the data reaches, computed once: the newest row, never past the ingest day. Every date preset and default
// window counts back from it, so a row the bank dated ahead moves none of them, and a window saved under a preset is
// still that preset.
describe("Indexes.dataThrough: every preset's one anchor", () => {
  const tx = txFactory();
  const GEN = "2026-10-31T18:00:00-04:00";
  const rows = [tx({ date: "2026-01-05" }), tx({ date: "2026-10-31" })];
  const ahead = tx({ date: "2026-11-02" });
  const anchor = buildIndexes([...rows, ahead], undefined, GEN).dataThrough!;

  it("is the newest row clamped to the ingest day: a row dated ahead leaves it there, and data that end earlier keep theirs", () => {
    expect(anchor).toBe("2026-10-31");
    expect(buildIndexes([...rows, ahead], undefined, GEN).dataRange!.last, "dataRange keeps the newest row itself").toBe("2026-11-02");
    expect(buildIndexes(rows, undefined, GEN).dataThrough).toBe(anchor);
    expect(buildIndexes([tx({ date: "2026-08-20" })], undefined, GEN).dataThrough).toBe("2026-08-20");
    expect(buildIndexes([...rows, ahead]).dataThrough, "no ingest stamp: the newest row").toBe("2026-11-02");
  });

  it("with a row dated ahead every preset's window is the one without it, and Explore's six months start where Cash Flow's do", () => {
    const without = buildIndexes(rows, undefined, GEN).dataThrough!;
    for (const p of SPENDING_PRESETS) expect(presetRange(p, anchor), p).toEqual(presetRange(p, without));
    expect(presetRange("6m", anchor)!.from).toBe("2026-05-01");
    expect(presetRange("6m", anchor, "month")!.from, "Cash Flow's 6M").toBe("2026-05-01");
    expect(presetRange("6m", "2026-11-02")!.from, "control: anchored on the row dated ahead it started a day late").toBe("2026-05-02");
  });

  it("at a year boundary, year-to-date is the ingest's year, never a year that has not begun", () => {
    const ix = buildIndexes([tx({ date: "2026-12-31" }), tx({ date: "2027-01-02" })], undefined, "2026-12-31T20:00:00-05:00");
    expect(ix.dataThrough).toBe("2026-12-31");
    expect(presetRange("ytd", ix.dataThrough)).toEqual({ from: "2026-01-01" });
    expect(presetRange("ytd", ix.dataRange!.last), "control: the newest row opened 2027").toEqual({ from: "2027-01-01" });
  });

  it("a view saved under a preset is that preset again: saved as win=, lit on the bar, read as the automatic window", () => {
    for (const p of ["3m", "6m", "12m", "ytd"] as const) {
      const from = presetRange(p, anchor)!.from;
      expect(savedFilterQuery({ ...EMPTY_FILTERS, from }, anchor), p).toBe(`win=${p}`);
      expect(rollingPreset(from, undefined, anchor), p).toBe(p);
      expect(matchPreset({ from }, anchor, SPENDING_PRESETS), p).toBe(p);
    }
    expect(isAutoWindow(presetRange("6m", anchor)!.from, undefined, anchor)).toBe(true);
  });
});

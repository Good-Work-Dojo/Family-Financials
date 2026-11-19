// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
// The first-run page in its three states, word for word, and the route wrapper that puts it in place of a route's page.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { Suspense } from "react";
import { MemoryRouter, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CODE_PILL } from "../components/NoData";
import { type LookOutcome, NOTHING_BUILT_RECHECK_MS } from "../data/DataContext";
import type { Setup } from "../data/types";
import type { FirstRunPage, FirstRunState } from "../lib/firstRun";

const ctx = vi.hoisted(() => ({ setup: null as unknown, fetched: null as string | null }));
vi.mock("../data/DataContext", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../data/DataContext")>()),
  useData: () => ({ data: { manifest: { setup: ctx.setup, simplefin_fetched_at: ctx.fetched, generated_at: "2026-08-11T09:00:00-06:00" },
    assumptions: { household: { timezone: "America/Denver" } } }, reload: async () => {} }),
}));
import { type PageDef, pageRoute } from "../App";
import { LOOK_FIRST_MS, RefreshProvider } from "../components/DataRefresh";
import FirstRun from "./FirstRun";

afterEach(() => { cleanup(); ctx.fetched = null; });

const NOTHING: Setup = { accounts: 0, feed: false, import_files: 0, manual_assets: 0 };
const show = (state: FirstRunPage, setup: Setup = NOTHING, onCheckAgain?: () => Promise<LookOutcome>) => {
  ctx.setup = setup;
  return render(<MemoryRouter><FirstRun state={state} onCheckAgain={onCheckAgain} /></MemoryRouter>);
};
const text = (id: string) => screen.getByTestId(id).textContent ?? "";
const items = (list: Element) => [...list.querySelectorAll("li")].map((li) => li.textContent);
/** a figure: an amount or a percentage */
const FIGURE = /\$\s?\d|\d\s?%/;

/** where the last step's ingest runs: once an ingest has built the dashboard, Data's rebuild button (a household listing
 *  no account has no feed); before, the ingest itself */
const REBUILD = "rebuild the dashboard with “Rebuild from files” on Data";
/** the three steps as a person reads them, keys included; `done` is the first step's line once an ingest has read it */
const STEPS = (done: string, then = done ? REBUILD : "re-run the ingest") => [
  "Describe the household: its people, the owner name for what they hold jointly, and its time zone. "
    + `(household.yaml in the config folder)${done}`,
  "List the accounts: each with a name, its institution, a kind and an owner — one of the household's people or the "
    + "joint label. (accounts.yaml in the same folder; kind: is checking, savings, credit-card, brokerage or loan)",
  "Bring data: link accounts to the bank feed by their feed ids, put their statement files in the statements folder "
    + `with the format each is in, or state an asset such as your home by hand; then ${then}. `
    + "(simplefin_id:; parser: and glob:; or manual_entries: in accounts.yaml)",
];
const DONE = "✓ Done: the last ingest read this file.";
/** where every key and how to run things are explained: the same sentence on the Set-up and the Waiting cards */
const DOCS_LINE = "Every key is described in docs/configuration.md; where each folder is, how to run the ingest, the "
  + "statement formats and how to set up the bank feed's access are in README.md.";
const DOCS = "Statement files are read only in the formats this release knows, and a brokerage's or a card's balance comes "
  + `from the bank feed alone. ${DOCS_LINE}`;
const steps = () => within(screen.getByTestId("card-first-run-steps")).getByRole("list");
/** where a value no account reports can be entered right away: on the Set-up and the Waiting cards, never before an
 *  ingest has built anything (there is no store to save to yet) */
const ENTER_BY_HAND = "A home, a vehicle or anything else no account reports can be entered now, in Settings → Manual assets.";

describe("the first-run page: set up", () => {
  it("one h1; the household is described and nothing is listed; three numbered steps, the first done", () => {
    show("setup");
    expect(screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent)).toEqual(["Set up your household"]);
    expect(text("card-first-run")).toBe("Set up your household"
      + "The household is described, but no account and no hand-stated asset is listed yet, so there is nothing to "
      + "show; until the first data arrives, this page stands in for every page but Data and Settings.");
    expect(steps().tagName, "the steps are an ordered list").toBe("OL");
    expect(items(steps())).toEqual(STEPS(DONE));
    expect(within(steps()).getByRole("link", { name: "Data" }).getAttribute("href"), "where the rebuild is done").toBe("/data");
    // the mark is words, not a colour alone, and the check mark itself is not read out
    const done = within(steps()).getByText("Done: the last ingest read this file.", { exact: false });
    expect(done.querySelector('[aria-hidden="true"]')?.textContent).toBe("✓ ");
    // the keys are hints in the pill the empty-state lines print code in, never the message
    const pills = [...steps().querySelectorAll("code")];
    expect(pills.map((c) => c.textContent)).toEqual(["household.yaml", "accounts.yaml", "kind:", "checking", "savings",
      "credit-card", "brokerage", "loan", "simplefin_id:", "parser:", "glob:", "manual_entries:", "accounts.yaml"]);
    expect(pills.every((c) => c.className === CODE_PILL)).toBe(true);
    expect(text("card-first-run-steps")).toContain(DOCS);
    expect(screen.queryByRole("button", { name: "Check now" }), "no look-again: the exports are there").toBeNull();
    // Settings stays open meanwhile, so a value no account reports can be entered there now
    expect(text("first-run-manual-assets")).toBe(ENTER_BY_HAND);
    expect(within(screen.getByTestId("first-run-manual-assets")).getByRole("link").getAttribute("href"))
      .toBe("/settings?card=manual-assets");
  });

  it("shows no figure, no chart, no summary and no command", () => {
    show("setup");
    const page = screen.getByTestId("first-run");
    expect(page.getAttribute("data-first-run")).toBe("setup");
    expect(page.textContent).not.toMatch(FIGURE);
    expect(page.querySelector('[data-testid="chart"]')).toBeNull();
    expect(screen.queryByTestId("card-first-run-summary")).toBeNull();
    expect(page.textContent).not.toMatch(/\bmake\b|python|npm|localStorage|\b\w+\.json\b/);
    expect(page.textContent, "the household is set up already: the set-up command is not offered").not.toMatch(/ff init/);
  });
});

describe("the first-run page: nothing built yet", () => {
  it("one h1, the three steps with none done, then what happens once they are", () => {
    const checkAgain = vi.fn(async (): Promise<LookOutcome> => "nothing-built");
    show("nothing-built", NOTHING, checkAgain);
    expect(screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent)).toEqual(["Set up your household"]);
    expect(text("card-first-run")).toBe("Set up your household"
      + "No ingest has built the dashboard's data yet, so there is nothing to show. Three steps set the household up "
      + "and bring its first data; the set-up command walks you through them (ff init).");
    // the command is the hint in the code pill, after the words that say what it does
    const pill = screen.getByTestId("card-first-run").querySelector("code");
    expect([pill?.textContent, pill?.className]).toEqual(["ff init", CODE_PILL]);
    expect(items(steps())).toEqual(STEPS(""));
    expect(steps().textContent).not.toMatch(/Done|✓/);
    expect(screen.queryByTestId("first-run-manual-assets"), "nothing is built: no store to save to yet").toBeNull();
    expect(text("card-first-run-steps")).toContain(DOCS);
    expect(text("card-first-run-steps")).toContain("Done all three? The dashboard opens here by itself once an ingest has "
      + "run without refusing the config; when the ingest refuses it, its own output lists every problem, one per line.");
    fireEvent.click(screen.getByRole("button", { name: "Check now" }));
    expect(checkAgain).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("first-run").getAttribute("data-first-run")).toBe("nothing-built");
    expect(screen.queryByRole("alert"), "no error screen").toBeNull();
  });

  it("the interval it names is the one the provider looks again at, in seconds", () => {
    show("nothing-built", NOTHING, async () => "nothing-built");
    expect(text("card-first-run-steps")).toContain(`This page looks again every ${NOTHING_BUILT_RECHECK_MS / 1000} seconds.`);
  });

  it("the status line is there, empty, before the first look, and only its text changes after it", async () => {
    show("nothing-built", NOTHING, async () => "nothing-built");
    const line = screen.getByRole("status");
    expect(line.textContent, "nothing said before a look").toBe("");
    fireEvent.click(screen.getByRole("button", { name: "Check now" }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByRole("status"), "the same element: a live region is never mounted already filled").toBe(line);
    expect(line.textContent).toBe("Looked again: still nothing built.");
  });

  it("a manual look that still finds nothing says so in the status line, which is cleared as the next look starts", async () => {
    let finish!: (outcome: LookOutcome) => void;
    const looks: LookOutcome[] = ["nothing-built"];
    show("nothing-built", NOTHING, () => (looks.length
      ? Promise.resolve(looks.shift()!) : new Promise<LookOutcome>((r) => { finish = r; })));
    fireEvent.click(screen.getByRole("button", { name: "Check now" }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByRole("status").textContent).toBe("Looked again: still nothing built.");
    fireEvent.click(screen.getByRole("button", { name: "Check now" }));
    expect(screen.getByRole("status").textContent, "cleared while the next look runs").toBe("");
    await act(async () => { finish("nothing-built"); });
    expect(screen.getByRole("status").textContent).toBe("Looked again: still nothing built.");
  });

  it("a manual look that failed (the exports unreachable, or there and unreadable) says so, and that a reload shows why", async () => {
    show("nothing-built", NOTHING, async () => "failed");
    fireEvent.click(screen.getByRole("button", { name: "Check now" }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByRole("status").textContent)
      .toBe("Looked again: the data could not be reached or read; reload the page for details.");
  });

  it("a later timed look that finds nothing built clears the failed look's line; a 'still nothing built' line stays", async () => {
    ctx.setup = NOTHING;
    const page = (quiet: number, outcome: LookOutcome) =>
      <MemoryRouter><FirstRun state="nothing-built" onCheckAgain={async () => outcome} quietLooks={quiet} /></MemoryRouter>;
    const view = render(page(0, "failed"));
    fireEvent.click(screen.getByRole("button", { name: "Check now" }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByRole("status").textContent).toMatch(/could not be reached or read/);
    view.rerender(page(1, "failed"));
    expect(screen.getByRole("status").textContent, "the exports were reached since: the line is no longer true").toBe("");
    view.unmount();
    const again = render(page(0, "nothing-built"));
    fireEvent.click(screen.getByRole("button", { name: "Check now" }));
    await act(async () => { await Promise.resolve(); });
    again.rerender(page(1, "nothing-built"));
    expect(screen.getByRole("status").textContent).toBe("Looked again: still nothing built.");
  });

  it("a manual look that loaded the data says nothing: the app takes the page's place", async () => {
    show("nothing-built", NOTHING, async () => "loaded");
    fireEvent.click(screen.getByRole("button", { name: "Check now" }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByRole("status").textContent).toBe("");
  });
});

describe("the first-run page: waiting for data", () => {
  const summary = () => items(within(screen.getByTestId("card-first-run-summary")).getByRole("list"));
  const paragraphs = () => [...screen.getByTestId("card-first-run-summary").querySelectorAll("p")].map((p) => p.textContent);
  const NEITHER = "Link an account to the bank feed by its feed id, or put its statement files in the statements folder "
    + `with the format it is in, then ${REBUILD} (simplefin_id:, or parser: and glob:, in accounts.yaml).`;
  const NOT_FETCHED = "Nothing has been fetched from the bank feed yet; the first ingest after a fetch brings its "
    + "balances and transactions.";
  const FETCHED = "The bank feed was fetched and reported nothing for these accounts: check each account's feed id "
    + "against the feed's own ids — Data lists every account the feed reported that no account names (simplefin_id: in "
    + "accounts.yaml).";

  it("one h1, then what is configured; with neither a feed id nor a file, where they go", () => {
    show("waiting", { ...NOTHING, accounts: 2 });
    expect(screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent)).toEqual(["Waiting for data"]);
    expect(text("card-first-run")).toBe("Waiting for data"
      + "The household is configured, and its first data has not arrived yet; until it does, this page stands in for "
      + "every page but Data and Settings.");
    expect(summary()).toEqual(["2 accounts configured", "No bank-feed id and no statement file yet"]);
    expect(paragraphs()).toEqual([NEITHER, ENTER_BY_HAND, DOCS_LINE, "What the last ingest read and reported is on Data."]);
    // the two documents are hints in the code pill, as on the Set-up card
    const docs = screen.getByTestId("card-first-run-summary").querySelectorAll("p")[2];
    expect([...docs.querySelectorAll("code")].map((c) => [c.textContent, c.className]))
      .toEqual([["docs/configuration.md", CODE_PILL], ["README.md", CODE_PILL]]);
    expect(screen.queryByTestId("card-first-run-steps")).toBeNull();
    expect(screen.getByTestId("first-run").getAttribute("data-first-run")).toBe("waiting");
  });

  it("points at Data with plain links: where the rebuild is done, and what the last ingest read", () => {
    show("waiting", { ...NOTHING, accounts: 1 });
    const links = screen.getAllByRole("link", { name: "Data" });
    expect(links.map((l) => [l.tagName, l.getAttribute("href")])).toEqual([["A", "/data"], ["A", "/data"]]);
  });

  it("says a value no account reports can be entered now, on Settings' Manual assets card", () => {
    show("waiting", { ...NOTHING, accounts: 1 });
    expect(text("first-run-manual-assets")).toBe(ENTER_BY_HAND);
    expect(screen.getByRole("link", { name: "Settings → Manual assets" }).getAttribute("href")).toBe("/settings?card=manual-assets");
  });

  it("a feed id never fetched: says nothing has been fetched, and what the first ingest after a fetch brings", () => {
    show("waiting", { ...NOTHING, accounts: 1, feed: true });
    expect(summary(), "a feed id is all the export says: no claim of a connection").toEqual(["1 account configured",
      "At least one account names a bank-feed id"]);
    expect(paragraphs()[0]).toBe(NOT_FETCHED);
  });

  it("a feed fetched that reported nothing for the accounts: check the feed ids against the feed's own", () => {
    ctx.fetched = "2026-06-10T15:00:00Z";
    show("waiting", { ...NOTHING, accounts: 2, feed: true });
    expect(paragraphs()[0]).toBe(FETCHED);
    expect([...screen.getByTestId("card-first-run-summary").querySelectorAll("p")[0].querySelectorAll("code")]
      .map((c) => c.textContent)).toEqual(["simplefin_id:", "accounts.yaml"]);
  });

  it("statement files found and no row read from them: says so, points at what Data says of each file, and to check their format", () => {
    show("waiting", { ...NOTHING, accounts: 3, import_files: 1 });
    expect(summary()).toEqual(["3 accounts configured", "1 statement file found"]);
    expect(paragraphs()[0]).toBe("The last ingest found them and read no rows from them (Data's What the last ingest read "
      + "says what each file held): check that each holds its account's statement rows in the format the account names "
      + "(parser: in accounts.yaml).");
    const link = screen.getByRole("link", { name: "What the last ingest read" });
    expect(link.tagName, "a plain link").toBe("A");
    expect(link.getAttribute("href")).toBe("/data?card=ingest-read");
    cleanup();
    show("waiting", { ...NOTHING, accounts: 1, import_files: 4 });
    expect(summary()).toEqual(["1 account configured", "4 statement files found"]);
  });

  it("a feed id and files: both are listed, and the feed's sentence is the one sentence", () => {
    show("waiting", { accounts: 2, feed: true, import_files: 2, manual_assets: 0 });
    expect(summary()).toEqual(["2 accounts configured", "At least one account names a bank-feed id", "2 statement files found"]);
    expect(paragraphs()[0]).toBe(NOT_FETCHED);
  });

  it("hand-stated assets are counted when there are any, and no account reads as none", () => {
    show("waiting", { ...NOTHING, manual_assets: 1 });
    expect(summary()).toEqual(["No account configured", "No bank-feed id and no statement file yet", "1 hand-stated asset"]);
    cleanup();
    show("waiting", { ...NOTHING, accounts: 1, feed: true, manual_assets: 2 });
    expect(summary()).toEqual(["1 account configured", "At least one account names a bank-feed id", "2 hand-stated assets"]);
  });

  it("shows no figure and no chart", () => {
    show("waiting", { accounts: 1_234, feed: true, import_files: 0, manual_assets: 1 });
    const page = screen.getByTestId("first-run");
    expect(page.textContent).not.toMatch(FIGURE);
    expect(page.textContent).toContain("1,234 accounts configured");
    expect(page.querySelector('[data-testid="chart"]')).toBeNull();
  });
});

// Inside the shell the page reads the shared refresh status: with a schedule in it and a feed, the next scheduled
// refresh on the household's clock (Denver here); with no schedule, no refresh service or no feed, nothing of one.
describe("the first-run page: waiting, and when the next scheduled refresh is", () => {
  const status = (schedule: unknown) => ({ state: "idle", trigger: null, request_nonce: null, queued_nonce: null,
    started_at: null, finished_at: null, rc: null, steps_failed: [], warnings: [], can_refresh: true, reason: "ok",
    retry_after_s: 0, used_today: 0, limit_per_day: 6, last_refresh_at: null, schedule,
    ingest: { wanted: false, last_started_at: null, last_finished_at: null, last_rc: null } });
  const SCHEDULE = { times: ["01:00", "09:00", "17:00"], timezone: "America/Denver" };
  async function waiting(setup: Setup, reply: { http: number; body: unknown }) {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-11T16:00:00Z"));                 // 10:00 in Denver
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify(reply.body), { status: reply.http }));
    ctx.setup = setup;
    render(<MemoryRouter><RefreshProvider><FirstRun state="waiting" /></RefreshProvider></MemoryRouter>);
    await act(async () => { await vi.advanceTimersByTimeAsync(LOOK_FIRST_MS); });   // the tab's first look
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  }
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
  const cycle = new Intl.DateTimeFormat(undefined, { hour: "numeric" }).resolvedOptions().hourCycle;
  const fivePm = cycle === "h23" || cycle === "h24" ? "17:00" : "5:00 PM";

  it("with a feed and a schedule: the next scheduled refresh, after the step it waits on", async () => {
    await waiting({ ...NOTHING, accounts: 1, feed: true }, { http: 200, body: status(SCHEDULE) });
    expect(text("first-run-next-refresh")).toBe(`The next scheduled refresh is at ${fivePm} (Mountain Time).`);
  });
  it("no schedule in the status, no refresh service, or no feed: nothing of one", async () => {
    await waiting({ ...NOTHING, accounts: 1, feed: true }, { http: 200, body: status(null) });
    expect(screen.queryByTestId("first-run-next-refresh")).toBeNull();
    cleanup();
    await waiting({ ...NOTHING, accounts: 1, feed: true }, { http: 503, body: { error: "refresh not configured" } });
    expect(screen.queryByTestId("first-run-next-refresh")).toBeNull();
    cleanup();
    await waiting({ ...NOTHING, accounts: 1, import_files: 2 }, { http: 200, body: status(SCHEDULE) });
    expect(screen.queryByTestId("first-run-next-refresh")).toBeNull();
  });
});

// The wrapper: App.tsx pageRoute with the household's first-run state, on a small manifest of the same shape as PAGES
describe("pageRoute in a first-run state", () => {
  const own = (name: string) => () => <div data-testid={`own-${name}`}>{name}</div>;
  const PAGES_LIKE: PageDef[] = [
    { to: "/", label: "Home", icon: "H", page: own("home") },
    { to: "/h", label: "Hub", icon: "B", hub: {
      indexLabel: "Index", index: own("index"),
      tabs: [{ path: "tab", label: "Tab", icon: "T", page: own("tab") }],
      extra: [{ path: ":id", page: own("extra") }],
    } },
    { to: "/data", label: "Data", icon: "D", page: own("data") },
    { to: "/settings", label: "Settings", icon: "S", page: own("settings") },
  ];
  function Tree({ firstRun }: { firstRun: FirstRunState | null }) {
    const { pathname } = useLocation();
    return (
      <Suspense fallback={<div aria-busy="true" />}>
        <Routes>{PAGES_LIKE.map((p) => pageRoute(p, pathname, firstRun))}</Routes>
        <output data-testid="where">{pathname}</output>
      </Suspense>
    );
  }
  const at = (path: string, firstRun: FirstRunState | null) => {
    ctx.setup = { ...NOTHING, accounts: 1 };
    return render(<MemoryRouter initialEntries={[path]}><Tree firstRun={firstRun} /></MemoryRouter>);
  };

  it("every route but Data and Settings shows the first-run page inside its own page root, at its own address", async () => {
    for (const [path, pageId] of [["/", "page-home"], ["/h", "page-h"], ["/h/tab", "page-h-tab"], ["/h/some-id", "page-h-id"]]) {
      at(path, "setup");
      const page = await screen.findByTestId("first-run");
      expect(screen.getByTestId(pageId).contains(page), `${path}: inside ${pageId}`).toBe(true);
      expect(screen.queryByTestId(/^own-/), `${path}: its own page is not rendered`).toBeNull();
      expect(screen.getByTestId("where").textContent, "no redirect").toBe(path);
      cleanup();
    }
  });

  it("a hub drops its shell: no tab bar and no second h1, only the first-run page's", async () => {
    at("/h/tab", "waiting");
    await screen.findByTestId("first-run");
    expect(screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent)).toEqual(["Waiting for data"]);
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(screen.getByTestId("hub-h"), "the hub's own root is still stamped").toBeDefined();
  });

  it("Data and Settings stay their own pages", () => {
    for (const [path, name] of [["/data", "data"], ["/settings", "settings"]]) {
      at(path, "setup");
      expect(screen.getByTestId(`own-${name}`)).toBeDefined();
      expect(screen.queryByTestId("first-run")).toBeNull();
      cleanup();
    }
  });

  it("with no first-run state every route is its own page, the hub with its shell", () => {
    at("/h/tab", null);
    expect(screen.getByTestId("own-tab")).toBeDefined();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Hub");
    expect(screen.queryByTestId("first-run")).toBeNull();
  });
});

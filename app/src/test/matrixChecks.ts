// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Test support: the five checks the route matrix (matrix.test.tsx) runs on every route of the app, written as functions
// of a mounted DOM so test/matrix.checks.test.tsx can pin each on hand-built DOM.
//   renders  the page settled and shows neither the error-boundary card pageRoute() wraps every page in
//            (components/SectionBoundary.tsx) nor the full-page "Couldn't load data" screen (data/DataContext.tsx)
//   values   no broken value in the visible text (a box's value included), in the title, aria-label, placeholder, alt
//            and aria-valuetext attributes, in the document's title or in the text of a chart option the route built
//            (axis labels, series and legend names, titles: the chart stand-in draws none of them): NaN, Invalid Date,
//            undefined, Infinity (and ∞, what Intl prints for it), a date starting 0000- or the sentinel year 9999,
//            each matched as a whole token
//   private  with privacy mode on, the privacy net's money scan (test/moneyScan.ts) finds nothing
//   console  no console.error call while the route mounts and settles (React's warnings arrive there too)
//   cards    every row of the capability manifest (capabilities.ts) pinned to the route with no `when` is on screen by
//            its test id: a card renders its shell, with an empty-state line, for every household. A row a stand-in on
//            screen names in its `insteadOf` may be absent (excusedIds). A route that shows what the app puts in place
//            of its page (pageIsReplaced) owes none of its own cards; where that is the first-run page, it owes that
//            page's cards in the state the page shows (firstRunRows), and where it is the module-off page, that page's
//            card (moduleOffRows), and it shows none of its own (strayCards).
// Nothing in the application imports this module.
import { act, waitFor } from "@testing-library/react";
import { format } from "node:util";
import { GATED_ROUTES, type PageDef } from "../App";
import { CAPABILITIES, type Capability } from "../capabilities";
import { type FirstRunState, replacedInFirstRun } from "../lib/firstRun";
import { optionHits, textHits } from "./moneyScan";

/** the routes the matrix mounts: every page, hub tab and hub extra of PAGES, as patterns (`/accounts/:id` stays one) */
export function matrixRoutes(pages: readonly PageDef[]): string[] {
  const out: string[] = [];
  for (const p of pages) {
    out.push(p.to);
    for (const t of p.hub?.tabs ?? []) out.push(`${p.to}/${t.path}`);
    for (const x of p.hub?.extra ?? []) out.push(`${p.to}/${x.path}`);
  }
  return out;
}

/** the error-boundary card's heading (components/SectionBoundary.tsx) and the failed first load's (DataProvider) */
export const BOUNDARY_HEADING = "This section failed to render";
export const LOAD_FAILURE_HEADING = "Couldn't load data";

const alerts = (root: ParentNode, heading: string): Element[] =>
  [...root.querySelectorAll('[role="alert"]')].filter((el) => (el.textContent ?? "").startsWith(heading));

/** a mounted route has settled: nothing is loading (no `[aria-busy=true]`, the skeletons' mark) and the page root
 *  (`pageId`, the page's test id), an error-boundary card or the failed load is on screen */
export function settledOn(root: ParentNode, pageId: string): boolean {
  if (root.querySelector('[aria-busy="true"]')) return false;
  return root.querySelector(`[data-testid="${pageId}"]`) !== null || alerts(root, BOUNDARY_HEADING).length > 0
    || alerts(root, LOAD_FAILURE_HEADING).length > 0;
}

/** waits (bounded) until the route mounted in the document has settled, lets the effects its settled render scheduled
 *  run (a late crash or a late skeleton) and waits again: `boundMs` in all for the two waits, the second never less
 *  than `floorMs`. The time spent is read from `performance.now()`, which keeps running while the suites' `Date` stands
 *  at the ingest instant (test/demoClock.ts). The reason the route is not settled, or null. */
export async function settleRoute(pageId: string, boundMs: number, floorMs = 1000): Promise<string | null> {
  const started = performance.now();
  const settled = async (ms: number): Promise<boolean> => {
    try {
      await waitFor(() => { if (!settledOn(document.body, pageId)) throw new Error("not settled"); }, { timeout: ms });
      return true;
    } catch {
      return false;
    }
  };
  let ok = await settled(boundMs);
  if (ok) {
    await act(async () => { await new Promise((r) => setTimeout(r, 25)); });
    ok = await settled(Math.max(floorMs, boundMs - (performance.now() - started)));
  }
  if (ok) return null;
  const after = `after ${Math.round((performance.now() - started) / 100) / 10} s`;
  return document.querySelector('[aria-busy="true"]') ? `still loading ${after}` : `no page root, error card or load failure ${after}`;
}

/** what a mounted route shows instead of its page, or null when it rendered: the boundary card (with the error it
 *  caught), the failed load (with the loader's error), or a missing page root (`pageId`, the page's test id) */
export function renderProblem(root: ParentNode, pageId: string): string | null {
  const failed = alerts(root, LOAD_FAILURE_HEADING)[0];
  if (failed) return `the data failed to load: ${failed.querySelector("p.font-mono")?.textContent ?? "(no message)"}`;
  const cards = alerts(root, BOUNDARY_HEADING);
  if (cards.length) {
    const caught = cards.map((c) => c.querySelector("p")?.textContent ?? "(no message)");
    return `${cards.length} error-boundary card(s): ${caught.join(" | ")}`;
  }
  if (!root.querySelector(`[data-testid="${pageId}"]`)) return `the page root ${pageId} is not on screen`;
  return null;
}

/** where the crashes an error boundary caught were thrown, from the console lines `watchConsoleErrors` recorded: the
 *  boundary logs "section failed to render: <error> (at <app frame>) at <component> …" (components/SectionBoundary.tsx,
 *  the frame as `watchConsoleErrors` shortens it), and its card prints the message alone. Each as
 *  "thrown in <component> at <file:line:col>", once. */
export function throwSites(lines: readonly string[]): string[] {
  const sites = lines.flatMap((l) => {
    const m = /^section failed to render: .*?\(at (src\/[^\s()]+)\)(?: at ([\w$.]+))?/.exec(l);
    return m ? [`thrown in ${m[2] ?? "(no component)"} at ${m[1]}`] : [];
  });
  return [...new Set(sites)];
}

// A broken value, as a whole token: a letter (or, for the sentinels, a digit or a joining "-" "." "," before them) on
// either side makes it part of a longer word or id, so "BANANA", "Nancy", "1999", "99,999", "x9999y" and a feed
// account's "ACT-demo-9999" stay quiet while "$NaN", "NaN%", "$NaNK", "undefined months", "−Infinity", "$∞",
// "0000-01-01" and "9999-12-31" are found. Case-sensitive: an all-caps bank descriptor is no JavaScript value. The
// register's descriptors and vendor names print card and reference numbers, so 9999 after "#", "*", "(", a minus sign,
// an ellipsis or a bullet, or after a run of bullets and one space ("****9999", "#9999", "(9999)", "−9999", "…9999",
// "•••• 9999") is a number too, and Infinity before a space and a capital letter is a name ("Infinity Insurance").
export const BROKEN: readonly { what: string; re: RegExp }[] = [
  { what: "NaN", re: /(?<![A-Za-z])NaN(?![a-z])/g },
  { what: "Invalid Date", re: /Invalid Date/g },
  { what: "undefined", re: /(?<![A-Za-z0-9_])undefined(?![a-z0-9_])/g },
  { what: "Infinity", re: /(?<![A-Za-z])Infinity(?![a-z]| [A-Z])/g },
  // Intl's NumberFormat prints Infinity as ∞ ("$∞", "∞ mo"); no copy of the app uses the sign
  { what: "∞", re: /∞/g },
  { what: "a 0000- date", re: /(?<![\w-])0000-/g },
  { what: "the sentinel year 9999", re: /(?<![\w.,#*(−…•-])(?<!• )9999(?![\w,]|\.\d)/g },
];
// The app's own prose that holds one of those words on purpose, blanked out exactly before the scan: Projections' gauge
// says its floor is undefined while no essential row is ticked (sections/Tools.tsx).
export const BROKEN_OK: readonly string[] = ["the floor is undefined"];

const ATTRIBUTES = ["title", "aria-label", "placeholder", "alt", "aria-valuetext"] as const;
/** a few characters either side of a match, so a failure says where the value sits */
const around = (s: string, at: number, length: number) =>
  `${at > 12 ? "…" : ""}${s.slice(Math.max(0, at - 12), at + length + 12)}${at + length + 12 < s.length ? "…" : ""}`;
const blankAllowed = (s: string) => BROKEN_OK.reduce((t, ok) => t.split(ok).join(" ".repeat(ok.length)), s);

/** each element's own text (its direct text nodes joined): one element's words never run into the next's, so a value
 *  at the end of one element and a letter at the start of the next stay two tokens */
function ownTexts(root: HTMLElement): string[] {
  const out: string[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
  for (let n: Node | null = walker.currentNode; n; n = walker.nextNode()) {
    const own = [...n.childNodes].filter((c) => c.nodeType === Node.TEXT_NODE).map((c) => c.textContent ?? "").join("");
    if (own.trim()) out.push(own);
  }
  return out;
}

/** every broken value under `root` (each element's own text, a box's value and the five attributes) and in `title`
 *  (the document's), each as `<where>: <what> in "<snippet>"` */
export function brokenValues(root: HTMLElement, title = ""): string[] {
  const sources: [string, string][] = ownTexts(root).map((t) => ["text", t]);
  for (const el of root.querySelectorAll([...ATTRIBUTES.map((a) => `[${a}]`), "input", "textarea"].join(","))) {
    for (const a of ATTRIBUTES) {
      const v = el.getAttribute(a);
      if (v) sources.push([`${el.tagName.toLowerCase()}[${a}]`, v]);
    }
    if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) sources.push([`${el.tagName.toLowerCase()}.value`, el.value]);
  }
  if (title) sources.push(["document.title", title]);
  const out = new Set<string>();
  for (const [where, s] of sources) for (const found of brokenIn(s)) out.add(`${where}: ${found}`);
  return [...out];
}

/** each broken value in one string, as `<what> in "<snippet>"` */
function brokenIn(s: string): string[] {
  const text = blankAllowed(s);
  return BROKEN.flatMap(({ what, re }) => [...text.matchAll(re)].map((m) => `${what} in "${around(s, m.index ?? 0, m[0].length)}"`));
}

const plainObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && [Object.prototype, null].includes(Object.getPrototypeOf(v));

/** every broken value in the text the chart options a route built carry (the stand-in chart draws none of it): each
 *  string leaf, walking arrays and plain objects and skipping functions (what a formatter prints is the private
 *  check's), as `chart <n> <path>: <what> in "<snippet>"`, n counting the recorded options from 1. Every render of a
 *  chart records its option again, so a finding is reported once, at the first option that holds it. */
export function chartValues(options: readonly unknown[]): string[] {
  const first = new Map<string, number>();
  options.forEach((option, i) => {
    const seen = new Set<object>();
    const walk = (v: unknown, path: string): void => {
      if (typeof v === "string") {
        for (const found of brokenIn(v)) if (!first.has(`${path}: ${found}`)) first.set(`${path}: ${found}`, i + 1);
      } else if (Array.isArray(v) || plainObject(v)) {
        if (seen.has(v)) return;
        seen.add(v);
        if (Array.isArray(v)) v.forEach((x, j) => walk(x, `${path}[${j}]`));
        else for (const [k, x] of Object.entries(v)) walk(x, path ? `${path}.${k}` : k);
      }
    };
    walk(option, "");
  });
  return [...first].map(([finding, n]) => `chart ${n} ${finding}`);
}

/** a check's findings in its failure message: the first `cap`, then how many more */
export const listed = (items: readonly string[], cap = 5): string =>
  items.slice(0, cap).join("; ") + (items.length > cap ? `; +${items.length - cap} more` : "");

/** the first-run page's marker (sections/FirstRun.tsx): its test id, and the attribute that holds its state */
export const FIRST_RUN_MARK = "first-run";

/** the module-off page's marker (components/ModuleOff.tsx): its test id */
export const MODULE_OFF_MARK = "module-off";

/** what the app shows in place of a route's page, by the test id it carries and the routes (patterns) it stands in on:
 *  the unknown-account card (sections/AccountPage.tsx) answers an account id no account has; the first-run page
 *  (sections/FirstRun.tsx) stands in for every page but Data and Settings while the household's exports hold nothing;
 *  the module-off page stands in for a page that is switched off, on the routes App.tsx PAGES gates */
export const REPLACED_PAGES: readonly { testId: string; on: (route: string) => boolean }[] = [
  { testId: "unknown-account", on: (route) => route === "/accounts/:id" },
  { testId: FIRST_RUN_MARK, on: replacedInFirstRun },
  { testId: MODULE_OFF_MARK, on: (route) => GATED_ROUTES.includes(route) },
];

const onScreen = (root: ParentNode, id: string) => root.querySelector(`[data-testid="${id}"]`) !== null;

/** the mounted route (its pattern) shows something in place of its page, so none of its own cards is looked for */
export function pageIsReplaced(root: ParentNode, route: string): boolean {
  return REPLACED_PAGES.some((r) => r.on(route) && onScreen(root, r.testId));
}

/** the state of the first-run page on screen under `root` (its marker's `data-first-run`), or null when it is not */
export function firstRunShown(root: ParentNode): FirstRunState | null {
  const state = root.querySelector(`[data-testid="${FIRST_RUN_MARK}"]`)?.getAttribute("data-first-run");
  return state === "setup" || state === "waiting" ? state : null;
}

/** the first-run page's rows in `state`: the `first-run` rows that name it or no state */
export const firstRunRows = (state: FirstRunState, rows: readonly Capability[] = CAPABILITIES): Capability[] =>
  rows.filter((c) => c.when === "first-run" && (c.firstRun === undefined || c.firstRun === state));

/** the module-off page is on screen under `root` */
export const moduleOffShown = (root: ParentNode): boolean => root.querySelector(`[data-testid="${MODULE_OFF_MARK}"]`) !== null;

/** the module-off page's rows: the `module-off` ones */
export const moduleOffRows = (rows: readonly Capability[] = CAPABILITIES): Capability[] =>
  rows.filter((c) => c.when === "module-off");

/** the rows the page standing in for the mounted route's own owes in its place: the first-run page's in the state it
 *  shows where it stands in, else the module-off page's where it stands in; null where neither does */
function standInRows(root: ParentNode, route: string, rows: readonly Capability[]): Capability[] | null {
  const firstRun = firstRunShown(root);
  if (firstRun !== null && replacedInFirstRun(route)) return firstRunRows(firstRun, rows);
  if (moduleOffShown(root) && GATED_ROUTES.includes(route)) return moduleOffRows(rows);
  return null;
}

/** the rows of `route` a stand-in on screen under `root` replaces: every id named in the `insteadOf` of a row of the
 *  route that is itself on screen. A row named by a stand-in that is not there is excused by nothing. */
export function excusedIds(root: ParentNode, route: string, rows: readonly Capability[] = CAPABILITIES): Set<string> {
  return new Set(rows.filter((c) => c.route === route && c.insteadOf && onScreen(root, c.id)).flatMap((c) => c.insteadOf ?? []));
}

/** the manifest rows the mounted route owes whose test id is nowhere under `root`, in manifest order: its rows with no
 *  `when`, but those a stand-in on screen replaces (excusedIds); on a route the first-run page or the module-off page
 *  stands in for, that page's rows (standInRows: the first-run page's in the state it shows) and none of the route's
 *  own; none on a route another replacement stands in for. A `when` row exists only under its condition and may be
 *  absent. */
export function missingCards(root: ParentNode, route: string, rows: readonly Capability[] = CAPABILITIES): string[] {
  const standIn = standInRows(root, route, rows);
  if (standIn !== null) return standIn.filter((c) => !onScreen(root, c.id)).map((c) => c.id);
  if (pageIsReplaced(root, route)) return [];
  const excused = excusedIds(root, route, rows);
  return rows.filter((c) => c.route === route && !c.when && !excused.has(c.id) && !onScreen(root, c.id)).map((c) => c.id);
}

/** the route's own cards on screen while the first-run page or the module-off page stands in for its page: its
 *  manifest rows other than those two pages', in manifest order; none on a route that shows its page. (The
 *  unknown-account card is itself the account page's header card, so this holds for those two pages alone.) */
export function strayCards(root: ParentNode, route: string, rows: readonly Capability[] = CAPABILITIES): string[] {
  if (standInRows(root, route, rows) === null) return [];
  return rows.filter((c) => c.route === route && c.when !== "first-run" && c.when !== "module-off" && onScreen(root, c.id))
    .map((c) => c.id);
}

/** the cards check on one visit: its failure message, or null when it passes. A visit that did not render (`problem`,
 *  the renders check's reason) fails, as no card was looked for; otherwise each missing card fails it, by id, and so
 *  does each card of the route's own page on screen where the first-run page stands in for that page (`stray`). */
export function cardsFailure(route: string, problem: string | null, missing: readonly string[],
  stray: readonly string[] = []): string | null {
  if (problem !== null) return `${route} did not render, so no card was looked for (see its renders check)`;
  const failures = [
    ...(missing.length ? [`${route} lacks ${missing.length} card(s) of its manifest rows: ${listed(missing)}`] : []),
    ...(stray.length ? [`${route} shows ${stray.length} card(s) of its own page where another stands in: ${listed(stray)}`] : []),
  ];
  return failures.length ? failures.join("; ") : null;
}

/** what the privacy net's money scan finds under `root` and in the chart options a route built (`route` is its pattern,
 *  the key of the net's year-axis allowance); an option's formatters are called with integer samples */
export function moneyHits(root: HTMLElement, route: string, options: readonly unknown[]): string[] {
  const stats = { formatters: 0 };
  return [...new Set([...textHits(root), ...options.flatMap((o) => optionHits(route, o, stats))])];
}

/** an Error as its name, message and the first frame in the app's own sources (where it was thrown), never its stack */
const shortError = (e: Error): string => {
  const frame = e.stack?.split("\n").filter((l) => !l.includes("node_modules"))
    .map((l) => /\/(src\/(?!test\/)[^\s():]+:\d+:\d+)/.exec(l)?.[1]).find(Boolean);
  return `${e.name}: ${e.message}${frame ? ` (at ${frame})` : ""}`;
};

/** console.error calls from now until `stop()`, each formatted as the console would print it (an Error argument
 *  shortened to its message and the app frame it was thrown at), on one line, with paths from `src/` or
 *  `node_modules/`, cut to 300 characters; `lines()` reads them so far, `stop()` gives console.error back and returns
 *  them */
export function watchConsoleErrors(): { lines: () => string[]; stop: () => string[] } {
  const original = console.error;
  const seen: string[] = [];
  console.error = (...args: unknown[]) => {
    const line = format(...args.map((a) => (a instanceof Error ? shortError(a) : a)))
      .replace(/(?:file:\/\/)?\/[^\s()]*?\/(src|node_modules)\//g, "$1/").replace(/\s+/g, " ").trim();
    seen.push(line.slice(0, 300));
  };
  return { lines: () => [...seen], stop: () => { console.error = original; return seen; } };
}

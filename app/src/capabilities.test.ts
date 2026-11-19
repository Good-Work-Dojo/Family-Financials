// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { CAPABILITIES, hubTestId, pageTestId } from "./capabilities";
import { replacedInFirstRun } from "./lib/firstRun";

// Reads the SOURCE (node:fs — vitest may stub `?raw` imports to empty strings), so deploy CI,
// which runs without exports and skips the smoke loop, still refuses a manifest id nothing tags.
const SRC = dirname(fileURLToPath(import.meta.url));
function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? sourceFiles(join(dir, e.name))
    : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [join(dir, e.name)] : []);
}
/** Manifest ids whose testid is assembled at runtime, so no `testId="<id>"` literal exists for
 *  them. Empty today: every id is a literal. Add one here, with the reason, only if that changes. */
const BUILT_AT_RUNTIME = new Set<string>([]);

describe("capability manifest", () => {
  it("ids are unique, well-formed, and never the EChart mock's id", () => {
    const ids = CAPABILITIES.map((c) => c.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) {
      expect(id, id).toMatch(/^(card|kpis|kpi)-[a-z0-9-]+$/);
      expect(id).not.toBe("chart");
    }
  });
  it("a stand-in is a `when: \"data\"` row whose `insteadOf` names rows of its own route that carry no `when`", () => {
    const standIns = CAPABILITIES.filter((c) => c.insteadOf !== undefined);
    expect(standIns.length).toBeGreaterThan(0);
    for (const s of standIns) {
      expect(s.when, `${s.id} is conditional itself`).toBe("data");
      expect((s.insteadOf ?? []).length > 0, `${s.id} names what it replaces`).toBe(true);
      for (const id of s.insteadOf ?? []) {
        const row = CAPABILITIES.find((c) => c.id === id);
        expect(row?.route, `${s.id} replaces ${id}, a row of its own route`).toBe(s.route);
        expect(row?.when, `${id}, which ${s.id} replaces, is an ordinary row`).toBeUndefined();
        expect(row?.insteadOf, `${id} is no stand-in itself`).toBeUndefined();
      }
    }
  });
  it("a first-run row is pinned to Home, a route the first-run page stands in for; each state has its rows, and only a "
    + "first-run row names a state", () => {
    const firstRun = CAPABILITIES.filter((c) => c.when === "first-run");
    for (const c of firstRun) {
      expect(c.route, `${c.id} is pinned to Home`).toBe("/");
      expect(replacedInFirstRun(c.route), `${c.id}: the first-run page stands in for its route`).toBe(true);
      expect(c.insteadOf, `${c.id} is no stand-in: the page it is on replaces the route's whole page`).toBeUndefined();
    }
    for (const state of ["setup", "waiting"] as const) {
      const rows = firstRun.filter((c) => c.firstRun === undefined || c.firstRun === state);
      expect(rows.length >= 2, `the ${state} page has its heading card and one of its own`).toBe(true);
      expect(rows.some((c) => c.firstRun === state), `the ${state} page has a card only it shows`).toBe(true);
    }
    for (const c of CAPABILITIES.filter((x) => x.firstRun !== undefined)) {
      expect(c.when, `${c.id} names a first-run state`).toBe("first-run");
    }
  });
  it("every route starts with a slash and has no trailing slash", () => {
    for (const c of CAPABILITIES) {
      expect(c.route, c.id).toMatch(/^\/([a-z0-9:-]+(\/[a-z0-9:-]+)*)?$/);
    }
  });
  it("derives page and hub testids from routes", () => {
    expect(pageTestId("/")).toBe("page-home");
    expect(pageTestId("/cash-flow")).toBe("page-cash-flow");
    expect(pageTestId("/cash-flow/income-statement")).toBe("page-cash-flow-income-statement");
    expect(pageTestId("/accounts/:id")).toBe("page-accounts-id");
    expect(hubTestId("/real-estate")).toBe("hub-real-estate");
  });
});

describe("capability ids are tagged in the source", () => {
  const files = sourceFiles(SRC);
  const literals = files
    .flatMap((f) => readFileSync(f, "utf8").match(/(?:data-testid|testId)="[a-z0-9-]+"/g) ?? [])
    .map((l) => l.slice(l.indexOf('"') + 1, -1));
  it("scans a non-trivial corpus, so an empty read cannot pass", () => {
    expect(files.length).toBeGreaterThan(50);
    expect(literals.length).toBeGreaterThan(80);
  });
  it("every capability id appears as a testId=\"…\" or data-testid=\"…\" literal under app/src", () => {
    const tagged = new Set(literals);
    for (const c of CAPABILITIES) {
      if (BUILT_AT_RUNTIME.has(c.id)) continue;
      expect(tagged.has(c.id), `${c.id} (${c.route}) is tagged nowhere in app/src`).toBe(true);
    }
  });
  // the other direction: a card, chart card or KPI grid on a page is a capability with its row, so a card removed from
  // the manifest cannot stay on its page unnoticed. A card that stands in for others is a row of its own too (`when`,
  // `insteadOf`), so this list of tagged literals that are no capability is empty; a name added to it must be such a
  // literal.
  const NOT_A_CAPABILITY = new Set<string>([]);
  it("every card / KPI testid literal under app/src is a manifest id (or a named stand-in)", () => {
    const ids = new Set(CAPABILITIES.map((c) => c.id));
    const cards = [...new Set(literals.filter((l) => /^(card|kpis|kpi)-/.test(l)))];
    expect(cards.length).toBeGreaterThan(80);
    for (const l of cards) expect(ids.has(l) || NOT_A_CAPABILITY.has(l), `${l} is on a page but has no capabilities.ts row`).toBe(true);
    for (const l of NOT_A_CAPABILITY) expect(cards.includes(l) && !ids.has(l), `${l}: the stand-in list names a real literal, not an id`).toBe(true);
  });
  it("the runtime-built allowlist names only real manifest ids", () => {
    const ids = new Set(CAPABILITIES.map((c) => c.id));
    for (const id of BUILT_AT_RUNTIME) expect(ids.has(id), id).toBe(true);
  });
});

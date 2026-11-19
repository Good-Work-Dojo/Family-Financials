// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it, vi } from "vitest";
import type { CategoryRename } from "../data/types";
import { __resetFilterWarnings } from "../filters/filters";
import type { SavedFilter, SavedReport } from "./prefs";
import { DEFAULT_SPEC } from "./reportSpec";
import { savedFilterPath, savedReportPath, saveRefusalText, staleOpenPath, validSavedFilters, validSavedReports } from "./savedFilters";

const f = (id: string, query: string): SavedFilter => ({ id, name: id, query, created_at: "2026-01-01T00:00:00Z" });
const CATS = ["Dining", "Groceries"];
const RENAMES: CategoryRename[] = [{ from: { category: "Eating Out" }, to: { category: "Dining" } }];

describe("validSavedFilters", () => {
  it("passes a view whose categories exist and keeps its query verbatim", () => {
    const { valid, stale } = validSavedFilters([f("a", "categories=Dining%7CGroceries&q=taco")], CATS, []);
    expect(valid.map((v) => v.id)).toEqual(["a"]);
    expect(valid[0].query).toBe("categories=Dining%7CGroceries&q=taco");
    expect(stale).toEqual([]);
  });
  it("rewrites a renamed category so the view keeps working under the new name", () => {
    const { valid } = validSavedFilters([f("a", "categories=Eating+Out")], CATS, RENAMES);
    expect(new URLSearchParams(valid[0].query).get("categories")).toBe("Dining");
  });
  it("a deleted category or an unknown key makes the view stale, with the reason", () => {
    const { valid, stale } = validSavedFilters([f("gone", "categories=Nightlife"), f("odd", "categories=Dining&bogus=1")], CATS, []);
    expect(valid).toEqual([]);
    expect(stale.map((s) => [s.filter.id, s.reason])).toEqual([
      ["gone", 'category "Nightlife" is no longer in the data'], ["odd", 'unknown filter key "bogus"']]);
  });
  it("an empty query is a valid all-time view, and the path is the Transactions tab", () => {
    expect(validSavedFilters([f("all", "")], CATS, []).valid).toHaveLength(1);
    expect(savedFilterPath(f("all", ""))).toBe("/spending/transactions");
    expect(savedFilterPath(f("a", "q=x"))).toBe("/spending/transactions?q=x");
  });
  it("splits a mixed list without losing or reordering anything: every view is in exactly one side", () => {
    const all = [f("1", "q=a"), f("2", "categories=Nope"), f("3", "categories=Groceries"), f("4", "x=1")];
    const { valid, stale } = validSavedFilters(all, CATS, []);
    expect(valid.map((v) => v.id)).toEqual(["1", "3"]);
    expect(stale.map((s) => s.filter.id)).toEqual(["2", "4"]);
  });
  it("a stale view keeps its own record (name, query) so Data can name it and delete it", () => {
    const { stale } = validSavedFilters([f("gone", "categories=Nightlife&q=x")], CATS, []);
    expect(stale[0].filter).toEqual(f("gone", "categories=Nightlife&q=x"));
  });
});

// a view saved on a preset window stores `win=<preset>` (filters/useSpendingDefaults savedFilterQuery); the validators must know it
describe("a rolling window (win=) in a stored query", () => {
  it("is a valid key, not an unknown one: the view is offered", () => {
    const { valid, stale } = validSavedFilters([f("a", "categories=Dining&win=6m")], CATS, []);
    expect(stale).toEqual([]);
    expect(valid.map((v) => v.id)).toEqual(["a"]);
    expect(valid[0].query).toBe("categories=Dining&win=6m");
  });
  it("an unknown win VALUE is not stale either (it is ignored with a warning when the view opens), but a junk KEY still is", () => {
    expect(validSavedFilters([f("a", "categories=Dining&win=bogus")], CATS, []).stale).toEqual([]);
    expect(validSavedFilters([f("a", "categories=Dining&wind=6m")], CATS, []).stale[0].reason).toBe('unknown filter key "wind"');
  });
  it("survives a category rename that rewrites the query", () => {
    const { valid } = validSavedFilters([f("a", "categories=Eating+Out&win=12m")], CATS, RENAMES);
    expect(valid[0].query).toBe("categories=Dining&win=12m");
  });
  it("a stale view's open-as-saved path keeps the window; a report's spec filters take win too", () => {
    expect(staleOpenPath(f("s", "categories=Nightlife&bogus=1&win=3m"))).toBe("/spending/transactions?categories=Nightlife&win=3m");
    const { valid, stale } = validSavedReports([r("x", "categories=Eating+Out&win=6m")], CATS, RENAMES);
    expect(stale).toEqual([]);
    expect(valid[0].spec.filters).toBe("categories=Dining&win=6m");
    expect(savedReportPath(valid[0])).toBe("/spending/reports?categories=Dining&win=6m&groupBy=vendor&interval=month&measure=spend&chart=table");
  });
});

describe("staleOpenPath", () => {
  it("opens a stale view with its own filters, dropping a key this version does not know", () => {
    expect(staleOpenPath(f("s", "categories=Nightlife&bogus=1&q=taco")))
      .toBe("/spending/transactions?categories=Nightlife&q=taco");
  });
  it("keeps a deleted category (the register then shows no rows for it) and a date window", () => {
    expect(staleOpenPath(f("s", "from=2025-02-01&categories=No+Such")))
      .toBe("/spending/transactions?categories=No+Such&from=2025-02-01");
  });
  it("parses defensively: a non-numeric amount is dropped, never written as NaN, and junk keys vanish", () => {
    __resetFilterWarnings();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const path = staleOpenPath(f("s", "min=abc&max=50&dir=sideways&__proto__=1"));
    expect(path).toBe("/spending/transactions?max=50");
    expect(path).not.toContain("NaN");
    expect(warn, "the dropped amount is said once").toHaveBeenCalledTimes(1);
    expect(staleOpenPath(f("s", "from=garbage&q=taco"))).toBe("/spending/transactions?q=taco");
    warn.mockRestore();
  });
  it("a query that is only unknown keys opens the bare tab (the caller adds the carried-search marker)", () => {
    expect(staleOpenPath(f("s", "bogus=1"))).toBe("/spending/transactions");
  });
});

const r = (id: string, filters: string, over: Partial<SavedReport["spec"]> = {}): SavedReport =>
  ({ id, name: id, spec: { ...DEFAULT_SPEC, filters, ...over }, created_at: "2026-01-01T00:00:00Z" });

describe("validSavedReports", () => {
  it("passes a report whose categories exist and keeps its spec verbatim (the same object, nothing rebuilt)", () => {
    const a = r("a", "categories=Dining&q=taco", { groupBy: "vendor", measure: "count" });
    const { valid, stale } = validSavedReports([a], CATS, []);
    expect(valid).toEqual([a]);
    expect(valid[0]).toBe(a);
    expect(stale).toEqual([]);
  });
  it("rewrites a renamed category inside the spec's filters and keeps every other spec field", () => {
    const { valid } = validSavedReports([r("a", "categories=Eating+Out", { groupBy: "owner", interval: "week", measure: "net", chart: "line" })], CATS, RENAMES);
    expect(valid[0].spec).toEqual({ filters: "categories=Dining", groupBy: "owner", interval: "week", measure: "net", chart: "line" });
    expect(valid[0].id).toBe("a");
  });
  it("a deleted category makes the report stale with the SAME reason text a stale view gets", () => {
    const { valid, stale } = validSavedReports([r("gone", "categories=Nightlife")], CATS, []);
    expect(valid).toEqual([]);
    expect(stale.map((s) => [s.report.id, s.reason])).toEqual([["gone", 'category "Nightlife" is no longer in the data']]);
    expect(stale[0].reason).toBe(validSavedFilters([f("gone", "categories=Nightlife")], CATS, []).stale[0].reason);
  });
  it("an empty-filter report is a valid all-time report; a mixed list keeps its order and loses nothing", () => {
    expect(validSavedReports([r("all", "")], CATS, []).valid).toHaveLength(1);
    const { valid, stale } = validSavedReports([r("1", "q=a"), r("2", "categories=Nope"), r("3", "categories=Groceries"), r("4", "x=1")], CATS, []);
    expect(valid.map((v) => v.id)).toEqual(["1", "3"]);
    expect(stale.map((s) => s.report.id)).toEqual(["2", "4"]);
  });
  it("a stale report keeps its own record (name, spec) so Data can name it and delete it", () => {
    const gone = r("gone", "categories=Nightlife&q=x", { groupBy: "vendor" });
    expect(validSavedReports([gone], CATS, []).stale[0].report).toBe(gone);
  });
});

describe("savedReportPath", () => {
  it("is the Reports tab with the saved filters and all four report keys (a link never leans on the default)", () => {
    expect(savedReportPath(r("a", "q=x", { groupBy: "account" }))).toBe("/spending/reports?q=x&groupBy=account&interval=month&measure=spend&chart=table");
    expect(savedReportPath(r("a", "from=2026-01-01&categories=Dining", { interval: "quarter", measure: "net", chart: "line" })))
      .toBe("/spending/reports?from=2026-01-01&categories=Dining&groupBy=vendor&interval=quarter&measure=net&chart=line");
  });
  it("a default spec with no filters still names its keys (the caller adds the carried-search marker)", () => {
    expect(savedReportPath(r("a", ""))).toBe("/spending/reports?groupBy=vendor&interval=month&measure=spend&chart=table");
  });
});

describe("saveRefusalText", () => {
  it("words each refusal in the kind's own terms; a name problem or no refusal says nothing", () => {
    expect(saveRefusalText("count", "view")).toBe("50 views saved — delete one on Data first");
    expect(saveRefusalText("count", "report")).toBe("50 reports saved — delete one on Data first");
    expect(saveRefusalText("full", "view")).toBe("Settings are full — delete a view on Data first");
    expect(saveRefusalText("full", "report")).toBe("Settings are full — delete a report on Data first");
    expect(saveRefusalText("entry", "view")).toBe("these filters are too long to save as a view");
    expect(saveRefusalText("entry", "report")).toBe("this report is too long to save");
    expect(saveRefusalText("name", "report")).toBeNull();
    expect(saveRefusalText(null, "view")).toBeNull();
  });
});

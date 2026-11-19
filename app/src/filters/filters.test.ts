// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Transaction } from "../data/types";
import { txFactory } from "../lib/__fixtures__";
import {
  __resetFilterWarnings, applyFilters, countActive, EMPTY_FILTERS, FILTER_PARAM_KEYS, Filters, isISODate, parseFilters,
  serializeFilters, spendingLink, spendingPath, writeFilters,
} from "./filters";

const tx = txFactory({ account: "demo_checking_0101", account_name: "Example Bank", description: "SOMETHING", vendor: "Amazon",
  category: "Shopping", amount: -50, source_file: "x.csv" });

const FIXTURE: Transaction[] = [
  tx({ date: "2025-01-10", vendor: "Amazon", category: "Shopping", amount: -25 }),
  tx({ date: "2025-02-10", vendor: "Maple Street Grocer", category: "Groceries", amount: -85, owner: "Alex", account: "demo_savings_0202" }),
  tx({ date: "2025-03-10", vendor: "Acme Payroll", category: "Income", amount: 5200, type: "income" }),
  tx({ date: "2025-04-10", vendor: "Venmo", category: "Cash & Misc", amount: -2500, needs_review: true }),
  tx({ date: "2025-05-10", vendor: "Example Card", category: "Internal", amount: -3200, type: "cc-payment", is_internal: true }),
  tx({ date: "2025-06-10", vendor: "Example Brokerage", category: "Internal", amount: -1000, type: "investment", is_internal: true }),
  tx({ date: "2026-01-05", vendor: "Little Oaks Daycare", category: "Kids", amount: -1350, tags: ["kids"] }),
];

const F = (over: Partial<Filters>): Filters => ({ ...EMPTY_FILTERS, ...over });

describe("applyFilters", () => {
  it("empty filters hide internal transfers/cc-payments only", () => {
    const out = applyFilters(FIXTURE, F({}));
    expect(out).toHaveLength(6);
    expect(out.every((t) => t.type !== "cc-payment" && t.type !== "transfer")).toBe(true);
  });

  it("includeInternal restores them", () => {
    expect(applyFilters(FIXTURE, F({ includeInternal: true }))).toHaveLength(7);
  });

  it("the sources facet keeps the rows filed the chosen ways, each group gathering its own sources", () => {
    const rows = [
      tx({ id: "r1", vendor: "Tallgrass Outfitters", category_source: "ai" }),
      tx({ id: "r2", vendor: "Maple Street Grocer", category_source: "vendor_rule" }),
      tx({ id: "r3", vendor: "Corner Bakery", category_source: "keyword" }),
      tx({ id: "r4", vendor: "Unknown Shop", category_source: "fallback" }),
      tx({ id: "r5", vendor: "Answered Shop", category_source: "answer" }),
    ];
    const ids = (f: Partial<Filters>) => applyFilters(rows, F(f)).map((t) => t.id);
    expect(ids({ sources: ["ai"] })).toEqual(["r1"]);
    expect(ids({ sources: ["rules"] })).toEqual(["r2", "r3"]);
    expect(ids({ sources: ["ai", "unsorted"] }), "values OR together").toEqual(["r1", "r4"]);
    expect(ids({ sources: ["answers"], vendors: ["Tallgrass Outfitters"] }), "and AND with the other facets").toEqual([]);
    expect(ids({}), "no group chosen: every row").toHaveLength(5);
  });

  it("date range is inclusive on both ends", () => {
    const out = applyFilters(FIXTURE, F({ from: "2025-02-10", to: "2025-03-10" }));
    expect(out.map((t) => t.date)).toEqual(["2025-02-10", "2025-03-10"]);
  });

  it("values within a facet OR together", () => {
    const out = applyFilters(FIXTURE, F({ categories: ["Shopping", "Groceries"] }));
    expect(out).toHaveLength(2);
  });

  it("facets AND together (category × owner)", () => {
    expect(applyFilters(FIXTURE, F({ categories: ["Groceries"], owners: ["Alex"] }))).toHaveLength(1);
    expect(applyFilters(FIXTURE, F({ categories: ["Groceries"], owners: ["Sam"] }))).toHaveLength(0);
  });

  it("amount range applies to |amount| and combines with direction", () => {
    const out = applyFilters(FIXTURE, F({ amountMin: 1000, direction: "expense" }));
    expect(out.map((t) => t.vendor)).toEqual(["Venmo", "Example Brokerage", "Little Oaks Daycare"]);
  });

  it("free-text search hits vendor and description, case-insensitive", () => {
    expect(applyFilters(FIXTURE, F({ q: "maple street grocer" }))).toHaveLength(1);
    expect(applyFilters(FIXTURE, F({ q: "someTHING" })).length).toBeGreaterThan(3);
  });

  it("needsReviewOnly + vendor combine", () => {
    const out = applyFilters(FIXTURE, F({ needsReviewOnly: true, vendors: ["Venmo"] }));
    expect(out).toHaveLength(1);
  });

  it("tag filter works", () => {
    expect(applyFilters(FIXTURE, F({ tags: ["kids"] }))).toHaveLength(1);
  });

  it("pairwise: date × category × account", () => {
    const out = applyFilters(FIXTURE, F({
      from: "2025-01-01", to: "2025-12-31",
      categories: ["Groceries", "Shopping"], accounts: ["demo_savings_0202"],
    }));
    expect(out).toHaveLength(1);
    expect(out[0].vendor).toBe("Maple Street Grocer");
  });
});

describe("URL round-trip", () => {
  it("serialize → parse is stable", () => {
    const f = F({
      from: "2025-01-01", to: "2025-12-31", categories: ["Kids", "Dining"],
      vendors: ["Maple Street Grocer"], amountMin: 10, direction: "expense", q: "taco",
      needsReviewOnly: true, tags: ["kids"],
    });
    const round = parseFilters(serializeFilters(f));
    expect(round).toEqual(f);
  });

  it("defaults are omitted from the URL", () => {
    expect(serializeFilters(F({})).toString()).toBe("");
  });

  it("the sources facet rides in the URL as its group ids, and a value that is no group filters nothing", () => {
    const f = F({ sources: ["ai", "unsorted"] });
    expect(serializeFilters(f).toString()).toBe("sources=ai%7Cunsorted");
    expect(parseFilters(serializeFilters(f))).toEqual(f);
    const junk = parseFilters(new URLSearchParams("sources=AI|vendor_rule|constructor|ai|ai"));
    expect(junk.sources, "only a known id, once").toEqual(["ai"]);
    const none = parseFilters(new URLSearchParams("sources=bogus"));
    expect(none.sources).toEqual([]);
    expect(countActive(none), "not an active filter").toBe(0);
    expect(countActive(F({ sources: ["ai"] }))).toBe(1);
  });

  it("vendors containing commas survive (| separator)", () => {
    const f = F({ vendors: ["Bed, Bath & Beyond"] });
    expect(parseFilters(serializeFilters(f)).vendors).toEqual(["Bed, Bath & Beyond"]);
  });
});

// A hand-edited or truncated link used to open an empty register (`from=garbage` compared as a string and matched nothing) or
// one filtered by NaN (`min=abc` counted as an active filter and matched everything), and said nothing about it.
describe("parseFilters drops values it cannot use, with one warning each", () => {
  let warn: ReturnType<typeof vi.spyOn>;
  beforeEach(() => { __resetFilterWarnings(); warn = vi.spyOn(console, "warn").mockImplementation(() => {}); });
  afterEach(() => { warn.mockRestore(); });
  const parse = (q: string) => parseFilters(new URLSearchParams(q));

  it("a non-numeric amount is dropped: no NaN bound, the filter is not active, every row still matches", () => {
    const f = parse("min=abc&max=NaN");
    expect(f.amountMin).toBeUndefined();
    expect(f.amountMax).toBeUndefined();
    expect(countActive(f)).toBe(0);
    expect(applyFilters(FIXTURE, f)).toHaveLength(6);
    expect(parse("min=Infinity").amountMin, "not finite").toBeUndefined();
  });

  it("a date that is not YYYY-MM-DD is dropped, so the register is not silently empty", () => {
    const f = parse("from=garbage&to=2025-3-1");
    expect(f.from).toBeUndefined();
    expect(f.to).toBeUndefined();
    expect(applyFilters(FIXTURE, f)).toHaveLength(6);
    expect(parse("from=2025-02-31").from, "there is no February 31st").toBeUndefined();
    expect(parse("to=2025-13-01").to, "there is no month 13").toBeUndefined();
  });

  it("good values are kept exactly (the control: a bad value is not dropped because everything is)", () => {
    const f = parse("from=2025-02-10&to=2025-03-10&min=12.5&max=0");
    expect(f).toMatchObject({ from: "2025-02-10", to: "2025-03-10", amountMin: 12.5, amountMax: 0 });
    expect(warn).not.toHaveBeenCalled();
    expect(isISODate("2024-02-29"), "a leap day is a day").toBe(true);
    expect(isISODate("2025-02-29")).toBe(false);
  });

  it("one console.warn per distinct bad value, however often the query is re-parsed", () => {
    for (let i = 0; i < 5; i++) parse("min=abc&from=garbage");
    expect(warn).toHaveBeenCalledTimes(2);
    parse("min=abc&from=garbage&max=xyz");
    expect(warn).toHaveBeenCalledTimes(3);
    const said = warn.mock.calls.map((c: unknown[]) => String(c[0])).join("\n");
    expect(said).toContain("?min=abc");
    expect(said).toContain("?from=garbage");
  });

  it("a bad value is also gone from what is written back (a filter edit cleans the URL)", () => {
    const f = parse("min=abc&from=garbage&categories=Dining");
    expect(serializeFilters(f).toString()).toBe("categories=Dining");
  });
});

describe("spendingLink", () => {
  it("targets the Transactions tab by default and the Explore tab on request", () => {
    expect(spendingLink({ vendors: ["V"] })).toBe("#/spending/transactions?vendors=V");
    expect(spendingLink({ vendors: ["V"] }, "explore")).toBe("#/spending?vendors=V");
    expect(spendingLink({}, "explore")).toBe("#/spending?");
  });
});

describe("spendingPath", () => {
  it("is the router path of the same tabs, and spendingLink is exactly '#' + it", () => {
    expect(spendingPath({ vendors: ["V"] })).toBe("/spending/transactions?vendors=V");
    expect(spendingPath({ vendors: ["V"] }, "explore")).toBe("/spending?vendors=V");
    for (const target of ["explore", "transactions"] as const) {
      expect(spendingLink({ q: "x", from: "2026-01-01" }, target)).toBe("#" + spendingPath({ q: "x", from: "2026-01-01" }, target));
    }
  });
});

// Required<Filters>, set to a non-default value each: a new Filters key is a tsc error here until
// it is listed (and then the tests below say whether FILTER_PARAM_KEYS carries its URL key)
const EVERY: Required<Filters> = {
  accounts: ["a"], owners: ["o"], categories: ["c"], vendors: ["v"], types: ["purchase"], tags: ["t"], sources: ["ai"],
  from: "2026-01-01", to: "2026-02-01", amountMin: 1, amountMax: 2, direction: "expense", q: "q",
  needsReviewOnly: true, includeInternal: true,
};

describe("FILTER_PARAM_KEYS", () => {
  it("is exactly the set of keys serializeFilters can write", () => {
    expect([...serializeFilters(EVERY).keys()].sort()).toEqual([...FILTER_PARAM_KEYS].sort());
  });
  it("leaves out the page-scoped keys other pages own", () => {
    for (const k of ["win", "snap", "pl", "end", "sort", "groupBy", "interval", "measure", "chart"]) expect(FILTER_PARAM_KEYS).not.toContain(k);
  });
});

describe("writeFilters", () => {
  it("replaces exactly the filter keys and keeps every other param (sort, snap, end, pl, win)", () => {
    const prev = new URLSearchParams("categories=A&q=x&sort=amount:asc&snap=2026-01-01&end=2026-05&pl=3m&win=6m");
    const next = writeFilters(prev, { ...EMPTY_FILTERS, vendors: ["V"] });
    expect(next.get("vendors")).toBe("V");
    expect(next.get("categories")).toBeNull();
    expect(next.get("q")).toBeNull();
    for (const k of ["sort", "snap", "end", "pl", "win"]) expect(next.get(k)).toBe(prev.get(k));
    expect(prev.get("categories")).toBe("A");                       // the input is not mutated
  });
  it("clearing a query with EVERY filter key set deletes them all: only the non-filter params are left", () => {
    // min/max/dir/review/internal included - a key missing from FILTER_PARAM_KEYS would linger in the URL
    const full = new URLSearchParams(serializeFilters(EVERY));
    expect(writeFilters(full, EMPTY_FILTERS).toString()).toBe("");
    full.set("sort", "amount:asc");
    full.set("snap", "2026-01-01");
    expect(writeFilters(full, EMPTY_FILTERS).toString()).toBe("sort=amount%3Aasc&snap=2026-01-01");
  });
  it("keeps the Reports tab's own keys (groupBy, interval, measure, chart) through every filter edit", () => {
    const prev = new URLSearchParams("categories=A&groupBy=vendor&interval=week&measure=net&chart=table");
    const next = writeFilters(prev, { ...EMPTY_FILTERS, q: "taco" });
    expect(next.toString()).toBe("groupBy=vendor&interval=week&measure=net&chart=table&q=taco");
    expect(writeFilters(prev, EMPTY_FILTERS).toString()).toBe("groupBy=vendor&interval=week&measure=net&chart=table");
  });
  it("clearing through it leaves only the non-filter params", () => {
    expect(writeFilters(new URLSearchParams("categories=A&sort=date:asc"), EMPTY_FILTERS).toString()).toBe("sort=date%3Aasc");
  });
});

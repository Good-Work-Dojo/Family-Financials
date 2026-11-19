// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { __resetSortWarnings, DEFAULT_SORT, parseSort, writeSort } from "./useSort";

const IDS = ["date", "vendor", "amount"] as const;
beforeEach(() => __resetSortWarnings());      // warn-once memory is module-level: every `it` starts with none
afterEach(() => vi.restoreAllMocks());

describe("sort <-> URL", () => {
  it("defaults to date descending and omits the default from the URL", () => {
    expect(parseSort(new URLSearchParams(""), IDS)).toEqual(DEFAULT_SORT);
    expect(writeSort(new URLSearchParams("q=x"), DEFAULT_SORT).toString()).toBe("q=x");
    expect(writeSort(new URLSearchParams("q=x&sort=amount:asc"), DEFAULT_SORT).toString()).toBe("q=x");
  });
  it("a non-default date sort is kept: only date descending is the default", () => {
    expect(writeSort(new URLSearchParams("q=x"), [{ id: "date", desc: false }]).get("sort")).toBe("date:asc");
    expect(parseSort(writeSort(new URLSearchParams(""), [{ id: "date", desc: false }]), IDS)).toEqual([{ id: "date", desc: false }]);
  });
  it("round-trips a column and direction as sort=<colId>:<asc|desc>", () => {
    const sp = writeSort(new URLSearchParams("q=x"), [{ id: "amount", desc: false }]);
    expect(sp.get("sort")).toBe("amount:asc");
    expect(sp.get("q")).toBe("x");
    expect(parseSort(sp, IDS)).toEqual([{ id: "amount", desc: false }]);
  });
  it("a stale or malformed sort falls back to the default with one warning, never a crash", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(parseSort(new URLSearchParams("sort=nope:asc"), IDS)).toEqual(DEFAULT_SORT);
    expect(parseSort(new URLSearchParams("sort=amount:sideways"), IDS)).toEqual(DEFAULT_SORT);
    expect(parseSort(new URLSearchParams("sort=amount"), IDS)).toEqual(DEFAULT_SORT);
    expect(warn).toHaveBeenCalledTimes(3);
  });
  it("a trailing segment is malformed too, not silently accepted", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(parseSort(new URLSearchParams("sort=amount:asc:extra"), IDS)).toEqual(DEFAULT_SORT);
    expect(warn).toHaveBeenCalledTimes(1);
  });
  // every URL write re-parses the query, so a bad ?sort= that stays in the URL while the user edits
  // filters must warn once per value, not once per write
  it("warns once per bad value, however often the same URL is parsed", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const bad = new URLSearchParams("sort=renamed-column:desc&q=x");
    for (let i = 0; i < 5; i++) expect(parseSort(bad, IDS)).toEqual(DEFAULT_SORT);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(parseSort(new URLSearchParams("sort=other-gone:asc"), IDS)).toEqual(DEFAULT_SORT);
    expect(warn, "a different bad value is its own warning").toHaveBeenCalledTimes(2);
  });
  it("the reset forgets what was warned about (a test-only hook)", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const bad = new URLSearchParams("sort=renamed-column:desc");
    parseSort(bad, IDS);
    parseSort(bad, IDS);
    expect(warn).toHaveBeenCalledTimes(1);
    __resetSortWarnings();
    parseSort(bad, IDS);
    expect(warn).toHaveBeenCalledTimes(2);
  });
  it("a valid sort never warns", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(parseSort(new URLSearchParams("sort=vendor:desc"), IDS)).toEqual([{ id: "vendor", desc: true }]);
    expect(warn).not.toHaveBeenCalled();
  });
});

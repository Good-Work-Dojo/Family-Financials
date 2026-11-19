// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { categoryTable, timeTable } from "./chartTable";

describe("chartTable", () => {
  it("categoryTable: one row per category, one column per series, gaps as null", () => {
    const t = categoryTable("Month", ["2026-01", "2026-02"], [{ name: "Income", data: [100, 200] }, { name: "Spend", data: [50] }]);
    expect(t.columns).toEqual(["Month", "Income", "Spend"]);
    expect(t.rows).toEqual([["2026-01", 100, 50], ["2026-02", 200, null]]);
  });
  it("timeTable: the union of dates, sorted, each series looked up by date", () => {
    const t = timeTable([{ name: "A", data: [["2026-01-02", 2], ["2026-01-01", 1]] }, { name: "B", data: [["2026-01-02", 20]] }]);
    expect(t.columns).toEqual(["Date", "A", "B"]);
    expect(t.rows).toEqual([["2026-01-01", 1, null], ["2026-01-02", 2, 20]]);
  });
  it("a gap is null, never 0 - and a real 0 stays 0 (undefined and null both read as a gap)", () => {
    const c = categoryTable("Month", ["a", "b", "c"], [{ name: "S", data: [0, undefined, null] }]);
    expect(c.rows).toEqual([["a", 0], ["b", null], ["c", null]]);
    const t = timeTable([{ name: "A", data: [["2026-01-01", 0]] }, { name: "B", data: [["2026-01-02", 5]] }]);
    expect(t.rows).toEqual([["2026-01-01", 0, null], ["2026-01-02", null, 5]]);
  });
  it("a series with no data still gets its column (all gaps), and an empty chart is an empty table", () => {
    expect(categoryTable("X", ["a"], [{ name: "S", data: [] }]).rows).toEqual([["a", null]]);
    expect(timeTable([]).rows).toEqual([]);
    expect(timeTable([], "Day").columns).toEqual(["Day"]);
  });
  it("neither helper aliases or mutates its inputs", () => {
    const data = [["2026-01-02", 2], ["2026-01-01", 1]] as [string, number][];
    const copy = JSON.parse(JSON.stringify(data));
    timeTable([{ name: "A", data }]);
    expect(data).toEqual(copy);
  });
});

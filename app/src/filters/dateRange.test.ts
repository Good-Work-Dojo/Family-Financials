// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  matchPreset, monthBounds, parseDateRange, parseWin, presetRange,
  writeDateRange, writeWin,
} from "./dateRange";
import { __resetFilterWarnings } from "./urlValues";

describe("parse/write round-trip", () => {
  it("writeDateRange patches from/to and preserves foreign params", () => {
    const sp = new URLSearchParams("cat=Dining&from=2025-01-01&win=6m");
    const next = writeDateRange(sp, { from: "2025-03-01", to: "2025-03-31" });
    expect(next.get("cat")).toBe("Dining");
    expect(next.get("from")).toBe("2025-03-01");
    expect(next.get("to")).toBe("2025-03-31");
    expect(next.get("win")).toBeNull();           // custom dates clear the preset
    expect(sp.get("from")).toBe("2025-01-01");    // input untouched
  });

  it("clearing a bound deletes its key", () => {
    const sp = new URLSearchParams("from=2025-01-01&to=2025-06-30");
    const next = writeDateRange(sp, { from: "2025-01-01" });
    expect(next.get("to")).toBeNull();
    expect(parseDateRange(next)).toEqual({ from: "2025-01-01", to: undefined });
  });

  it("writeWin sets the preset and clears explicit dates", () => {
    const sp = new URLSearchParams("from=2025-01-01&q=coffee");
    const next = writeWin(sp, "all");
    expect(parseWin(next)).toBe("all");
    expect(next.get("from")).toBeNull();
    expect(next.get("q")).toBe("coffee");
  });

  it("parseWin rejects unknown values", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(parseWin(new URLSearchParams("win=bogus"))).toBeNull();
    warn.mockRestore();
  });
});

describe("presetRange", () => {
  const MAX = "2026-06-04";
  it("all is open on both ends", () => {
    expect(presetRange("all", MAX)).toEqual({});
  });
  it("ytd starts Jan 1 of the max-date year", () => {
    expect(presetRange("ytd", MAX)).toEqual({ from: "2026-01-01" });
  });
  it("Nm subtracts calendar months from maxDate, leaving `to` open", () => {
    expect(presetRange("6m", MAX)).toEqual({ from: "2025-12-04" });
    expect(presetRange("12m", MAX)).toEqual({ from: "2025-06-04" });
  });
  it("matchPreset recovers the preset from a concrete range", () => {
    expect(matchPreset({ from: "2025-12-04" }, MAX, ["6m", "12m", "all"])).toBe("6m");
    expect(matchPreset({}, MAX, ["6m", "all"])).toBe("all");
    expect(matchPreset({ from: "2025-12-05" }, MAX, ["6m", "all"])).toBeNull();
  });
  it("month granularity counts buckets including the current month", () => {
    expect(presetRange("6m", MAX, "month")).toEqual({ from: "2026-01-01" });
    expect(presetRange("12m", "2026-06-30", "month")).toEqual({ from: "2025-07-01" });
    expect(presetRange("24m", "2026-01-31", "month")).toEqual({ from: "2024-02-01" });
    expect(matchPreset({ from: "2026-01-01" }, MAX, ["6m", "all"], "month")).toBe("6m");
  });
  it("with no data date no preset that counts back resolves, at either granularity, and no range matches one", () => {
    for (const p of ["3m", "6m", "12m", "24m", "ytd"] as const) {
      expect(presetRange(p, null), p).toBeNull();
      expect(presetRange(p, null, "month"), p).toBeNull();
    }
    expect(matchPreset({ from: "2026-01-01" }, null, ["6m", "all"], "month")).toBeNull();
    expect(matchPreset({}, null, ["6m"])).toBeNull();
  });
  it("All needs no anchor date: with no data date it is still the empty window, and no window matches it", () => {
    expect(presetRange("all", null)).toEqual({});
    expect(presetRange("all", null, "month")).toEqual({});
    expect(matchPreset({}, null, ["6m", "all"])).toBe("all");
  });
});

describe("monthBounds", () => {
  it("returns real month ends, not lexicographic hacks", () => {
    expect(monthBounds("2026-02")).toEqual(["2026-02-01", "2026-02-28"]);
    expect(monthBounds("2024-02")).toEqual(["2024-02-01", "2024-02-29"]);
    expect(monthBounds("2025-12")).toEqual(["2025-12-01", "2025-12-31"]);
  });
});

// Cash Flow and Recurring read ?from=&to= through parseDateRange. `from=garbage` used to be an explicit range matching nothing and `from=`
// an explicit EMPTY range; both now read as no bound, with one warning, and the page falls through to ?win= or its own default.
describe("parseDateRange drops what is not a day", () => {
  let warn: ReturnType<typeof vi.spyOn>;
  beforeEach(() => { __resetFilterWarnings(); warn = vi.spyOn(console, "warn").mockImplementation(() => {}); });
  afterEach(() => { warn.mockRestore(); });
  const parse = (q: string) => parseDateRange(new URLSearchParams(q));

  it("garbage, a two-digit-year or unpadded date, an impossible day and a month 13 are each dropped with a warning", () => {
    for (const bad of ["garbage", "25-01-01", "2025-3-1", "2025-02-31", "2025-13-01", "2025-01-01T00:00:00Z", "2025-01"]) {
      expect(parse(`from=${bad}`), `from=${bad}`).toEqual({ from: undefined, to: undefined });
      expect(parse(`to=${bad}`), `to=${bad}`).toEqual({ from: undefined, to: undefined });
    }
    expect(warn).toHaveBeenCalled();
  });
  it("an empty from= or to= is no bound (not an explicit empty range), and says nothing", () => {
    expect(parse("from=&to=")).toEqual({ from: undefined, to: undefined });
    expect(warn).not.toHaveBeenCalled();
  });
  it("good days are kept exactly - the control: a bad value is not dropped because everything is", () => {
    expect(parse("from=2025-02-10&to=2025-03-10")).toEqual({ from: "2025-02-10", to: "2025-03-10" });
    expect(parse("from=2024-02-29").from, "a leap day").toBe("2024-02-29");
    expect(warn).not.toHaveBeenCalled();
  });
  it("one bad end does not take the other with it", () => {
    expect(parse("from=garbage&to=2025-03-10")).toEqual({ from: undefined, to: "2025-03-10" });
  });
  it("one warning per distinct value, however often the query is parsed", () => {
    for (let i = 0; i < 4; i++) parse("from=garbage");
    expect(warn).toHaveBeenCalledTimes(1);
  });
  it("parseWin ignores an unknown preset with one warning, and 'constructor' is not a preset", () => {
    expect(parseWin(new URLSearchParams("win=bogus"))).toBeNull();
    expect(parseWin(new URLSearchParams("win=constructor"))).toBeNull();
    expect(parseWin(new URLSearchParams("win=bogus"))).toBeNull();
    expect(warn).toHaveBeenCalledTimes(2);
    expect(parseWin(new URLSearchParams("win=12m"))).toBe("12m");
    expect(parseWin(new URLSearchParams(""))).toBeNull();
    expect(warn).toHaveBeenCalledTimes(2);
  });
});

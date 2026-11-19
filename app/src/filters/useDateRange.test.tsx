// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { presetRange } from "./dateRange";
import { useDateRange } from "./useDateRange";
import { __resetFilterWarnings } from "./urlValues";

const MAX = "2026-06-30";
// the day the data reaches (Indexes.dataThrough, the presets' one anchor); a test sets it to null for a household with no
// transaction yet
const world = vi.hoisted(() => ({ dataThrough: "2026-06-30" as string | null }));
vi.mock("../data/DataContext", () => ({ useData: () => ({ indexes: { dataThrough: world.dataThrough } }) }));

const at = (search: string, preset: "6m" | "all" = "all", granularity: "day" | "month" = "day") =>
  renderHook(() => useDateRange(preset, granularity), { wrapper: ({ children }: { children: ReactNode }) => <MemoryRouter initialEntries={[`/cash-flow${search}`]}>{children}</MemoryRouter> }).result.current;

// The page-scoped range (Cash Flow, Recurring): a bad ?from= must fall through to ?win= or the page's own default, never become an
// explicit empty or garbage range.
describe("useDateRange with a malformed ?from=", () => {
  let warn: ReturnType<typeof vi.spyOn>;
  beforeEach(() => { __resetFilterWarnings(); warn = vi.spyOn(console, "warn").mockImplementation(() => {}); });
  afterEach(() => { cleanup(); warn.mockRestore(); });

  it("garbage from= with no window: the page default, and the range is NOT explicit", () => {
    const r = at("?from=garbage", "6m");
    expect(r.range).toEqual(presetRange("6m", MAX));
    expect(r.explicit).toBe(false);
  });
  it("garbage from= beside a ?win= preset: the preset", () => {
    const r = at("?from=garbage&win=12m");
    expect(r.range).toEqual(presetRange("12m", MAX));
    expect(r.explicit).toBe(true);
  });
  it("an empty from= is not an explicit empty range either", () => {
    const r = at("?from=", "6m");
    expect(r.range).toEqual(presetRange("6m", MAX));
    expect(r.explicit).toBe(false);
  });
  it("a good from= is the range, as before", () => {
    const r = at("?from=2026-01-05&to=2026-03-01");
    expect(r.range).toEqual({ from: "2026-01-05", to: "2026-03-01" });
    expect(r.explicit).toBe(true);
  });
});

// The anchor is the data's last day, the one every window shares (Indexes.dataThrough: a row the bank dated after the
// ingest day never moves it), so Cash Flow's 6M stays six months that hold data.
describe("useDateRange anchors its presets at the data's last day", () => {
  afterEach(() => { cleanup(); world.dataThrough = MAX; });

  it("counts back from Indexes.dataThrough, at either granularity", () => {
    const r = at("", "6m", "month");
    expect(r.maxDate).toBe(MAX);
    expect(r.range).toEqual(presetRange("6m", MAX, "month"));
    expect(r.range.from).toBe("2026-01-01");
    world.dataThrough = "2026-05-20";
    expect(at("", "6m", "month").maxDate).toBe("2026-05-20");
    expect(at("?win=12m").range).toEqual(presetRange("12m", "2026-05-20"));
  });
});

// A household with no transaction yet: there is no newest date to anchor a preset at, so the page's default and a
// `?win=` both read as no bound - never a window computed from a missing date - and no preset is lit.
describe("useDateRange with no transaction yet", () => {
  beforeEach(() => { world.dataThrough = null; });
  afterEach(() => { cleanup(); world.dataThrough = MAX; });

  it("the page default is no bound at all, at either granularity, and the anchor is null", () => {
    for (const g of ["day", "month"] as const) {
      const r = at("", "6m", g);
      expect(r.range, g).toEqual({});
      expect(r.maxDate, g).toBeNull();
      expect(r.explicit, g).toBe(false);
    }
  });
  it("a ?win= is no bound either, and of the chips only All is lit: it is the window shown", () => {
    const r = at("?win=12m");
    expect(r.range).toEqual({});
    expect(r.active(["6m", "12m", "all"])).toBe("all");
    expect(at("", "6m").active(["6m", "12m", "all"]), "the page's default chip is not lit; All is").toBe("all");
    expect(at("", "6m").active(["6m", "12m"]), "a page that offers no All lights nothing").toBeNull();
  });
  it("dates that arrived in the URL light nothing, and All drops them: its window needs no anchor", () => {
    const r = at("?from=2026-01-05&to=2026-03-01");
    expect(r.active(["6m", "all"])).toBeNull();
    expect(at("?win=all").range).toEqual({});
  });
  it("dates in the URL are kept exactly", () => {
    expect(at("?from=2026-01-05&to=2026-03-01").range).toEqual({ from: "2026-01-05", to: "2026-03-01" });
  });
});

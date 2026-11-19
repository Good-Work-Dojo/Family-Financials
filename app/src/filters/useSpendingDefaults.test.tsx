// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CARRIED_SEARCH } from "../lib/hubNav";
import { presetRange } from "./dateRange";
import { useFilters } from "./useFilters";
import { __resetFilterWarnings, EMPTY_FILTERS, parseFilters } from "./filters";
import { isAutoWindow, rollingPreset, savedFilterQuery, savedQuery, useSpendingDefaults, withoutAutoWindow } from "./useSpendingDefaults";

const MAX_DATE = "2026-09-30";
// the day the data reaches (Indexes.dataThrough, every preset's anchor); a test moves it to say "a refresh has landed
// since the view was saved", or sets it to null for a household with no transaction yet
const data = vi.hoisted(() => ({ end: "2026-09-30" as string | null }));
vi.mock("../data/DataContext", () => ({
  useData: () => ({ indexes: { dataRange: data.end === null ? null : { first: "2025-01-01", last: data.end }, dataThrough: data.end } }),
}));
const DEFAULT_FROM = presetRange("6m", MAX_DATE).from!;

let filters: ReturnType<typeof useFilters>;
let go: ReturnType<typeof useNavigate>;
function Loc() {
  const { search, state } = useLocation();
  return <output data-testid="loc">{search}|{JSON.stringify(state)}</output>;
}
function Explore() {
  filters = useFilters();
  useSpendingDefaults();
  go = useNavigate();
  return <Loc />;
}
function Elsewhere() {
  go = useNavigate();
  return <Loc />;
}
const mount = (search: string, state?: unknown) => render(
  <MemoryRouter initialEntries={[{ pathname: "/spending", search, state }]}>
    <Routes>
      <Route path="/spending" element={<Explore />} />
      <Route path="/spending/recurring" element={<Elsewhere />} />
    </Routes>
  </MemoryRouter>);
const loc = () => {
  const [search, state] = screen.getByTestId("loc").textContent!.split("|");
  return { sp: new URLSearchParams(search), search, state: JSON.parse(state) as unknown };
};
afterEach(() => { cleanup(); data.end = MAX_DATE; });

describe("useSpendingDefaults: what counts as a fresh entry", () => {
  it("a bare URL gets the six-month default", () => {
    mount("");
    expect(loc().sp.get("from")).toBe(DEFAULT_FROM);
  });
  it("a URL carrying only a page-scoped key (?sort=, ?groupBy=) is still fresh: default applied, the key kept", () => {
    mount("?sort=amount:asc");
    expect(loc().sp.get("from")).toBe(DEFAULT_FROM);
    expect(loc().sp.get("sort")).toBe("amount:asc");
    cleanup();
    mount("?groupBy=category");
    expect(loc().sp.get("from")).toBe(DEFAULT_FROM);
    expect(loc().sp.get("groupBy")).toBe("category");
  });
  it("a deep link with any filter key keeps its own state", () => {
    mount("?categories=Dining");
    expect(loc().search).toBe("?categories=Dining");
    expect(loc().state).toBeNull();
    cleanup();
    mount("?q=x&sort=amount:asc");
    expect(loc().search).toBe("?q=x&sort=amount:asc");
  });
  it("an entry carrying CARRIED_SEARCH keeps whatever filter state it has: cleared stays cleared", () => {
    mount("", CARRIED_SEARCH);
    expect(loc().search).toBe("");
    cleanup();
    mount("?sort=amount:asc", CARRIED_SEARCH);                       // cleared, but sorted
    expect(loc().search).toBe("?sort=amount:asc");
    cleanup();
    mount("?groupBy=category", CARRIED_SEARCH);
    expect(loc().search).toBe("?groupBy=category");
  });
  it("the default write itself marks the entry, like every filter write", () => {
    mount("");
    expect(loc().state).toEqual({ carriedSearch: true });
  });
});

describe("Back into a cleared tab", () => {
  it("does not re-apply the six-month default (clear marks its own history entry)", () => {
    mount("");
    expect(loc().sp.get("from")).toBe(DEFAULT_FROM);                 // fresh visit: default
    act(() => filters[2]());                                         // the user clears
    expect(loc().search).toBe("");
    act(() => go("/spending/recurring"));                            // another tab ...
    act(() => go(-1));                                               // ... and Back
    expect(loc().search, "the cleared state survives Back").toBe("");
  });
  it("same for an All-time pick that leaves a sort behind", () => {
    mount("?sort=vendor:asc");
    expect(loc().sp.get("from")).toBe(DEFAULT_FROM);
    act(() => filters[1]({ from: undefined, to: undefined }));       // the All preset
    expect(loc().search).toBe("?sort=vendor%3Aasc");
    act(() => go("/spending/recurring"));
    act(() => go(-1));
    expect(loc().search).toBe("?sort=vendor%3Aasc");
  });
});

// What a saved view or report keeps of the date window. Dates frozen on the day of the save made "Groceries, last 6 months" grow
// forever; a window that IS a preset is stored as `win=<preset>` and resolved against the data's end when it is opened.
describe("saving: a preset window is stored as a rolling `win`, a window the user picked as its dates", () => {
  const f = (over: object) => ({ ...EMPTY_FILTERS, categories: ["Dining"], ...over });
  it("the automatic six months, saved from a fresh visit, becomes win=6m - no `from`", () => {
    expect(savedFilterQuery(f({ from: DEFAULT_FROM }), MAX_DATE)).toBe("categories=Dining&win=6m");
    expect(savedQuery(`categories=Dining&from=${DEFAULT_FROM}`, MAX_DATE)).toBe("categories=Dining&win=6m");
    expect(savedQuery(`from=${DEFAULT_FROM}`, MAX_DATE)).toBe("win=6m");
  });
  it("every preset the bar offers is recognised: 3M, 12M and YTD are rolling too", () => {
    for (const p of ["3m", "12m", "ytd"] as const) {
      expect(savedFilterQuery(f({ from: presetRange(p, MAX_DATE).from }), MAX_DATE), p).toBe(`categories=Dining&win=${p}`);
      expect(rollingPreset(presetRange(p, MAX_DATE).from, undefined, MAX_DATE)).toBe(p);
    }
  });
  it("explicit dates are saved as the exact days: another start, or any end", () => {
    expect(savedFilterQuery(f({ from: "2025-11-03" }), MAX_DATE)).toBe("categories=Dining&from=2025-11-03");
    expect(savedFilterQuery(f({ from: DEFAULT_FROM, to: "2026-08-31" }), MAX_DATE)).toBe(`categories=Dining&from=${DEFAULT_FROM}&to=2026-08-31`);
    expect(savedQuery("from=2025-11-03", MAX_DATE)).toBe("from=2025-11-03");
    expect(savedQuery(`from=${DEFAULT_FROM}&to=2026-08-31`, MAX_DATE)).toBe(`from=${DEFAULT_FROM}&to=2026-08-31`);
  });
  it("All (no window) stays exactly as it was", () => {
    expect(savedFilterQuery(f({}), MAX_DATE)).toBe("categories=Dining");
    expect(savedQuery("categories=Dining", MAX_DATE)).toBe("categories=Dining");
    expect(rollingPreset(undefined, undefined, MAX_DATE)).toBeNull();
  });
});

describe("opening a saved view: `win` resolves against the data's end, once", () => {
  const OLD_END = "2026-06-30";
  // saved when the data ended 30 Jun, opened when it ends 30 Sep
  const saved = (from: string, end: string) => savedFilterQuery({ ...EMPTY_FILTERS, categories: ["Dining"], from }, end);
  let warn: ReturnType<typeof vi.spyOn>;
  beforeEach(() => { __resetFilterWarnings(); warn = vi.spyOn(console, "warn").mockImplementation(() => {}); });
  afterEach(() => { warn.mockRestore(); });

  it("saved on the default 6M, it reopens on a rolling 6M: the start moved with the data, `win` is gone, the marker is kept", () => {
    const query = saved(presetRange("6m", OLD_END).from!, OLD_END);
    expect(query).toBe("categories=Dining&win=6m");
    data.end = "2026-09-30";
    mount(`?${query}`, CARRIED_SEARCH);
    expect(loc().sp.get("from")).toBe(presetRange("6m", "2026-09-30").from);
    expect(loc().sp.get("from"), "not the day it was saved").not.toBe(presetRange("6m", OLD_END).from);
    expect(loc().sp.has("win")).toBe(false);
    expect(loc().sp.get("categories")).toBe("Dining");
    expect(loc().state).toEqual({ carriedSearch: true });
  });
  it("saved on 12M it reopens on a rolling 12M (and YTD on this year's start)", () => {
    data.end = "2026-09-30";
    mount(`?${saved(presetRange("12m", OLD_END).from!, OLD_END)}`, CARRIED_SEARCH);
    expect(loc().sp.get("from")).toBe(presetRange("12m", "2026-09-30").from);
    cleanup();
    mount(`?${saved(presetRange("ytd", OLD_END).from!, OLD_END)}`, CARRIED_SEARCH);
    expect(loc().sp.get("from")).toBe("2026-01-01");
  });
  it("saved with explicit dates it reopens on exactly those dates, whatever the data's end", () => {
    const query = saved("2025-11-03", OLD_END);
    expect(query).toBe("categories=Dining&from=2025-11-03");
    data.end = "2026-09-30";
    mount(`?${query}`, CARRIED_SEARCH);
    expect(loc().sp.get("from")).toBe("2025-11-03");
    expect(loc().sp.has("win")).toBe(false);
  });
  it("`win=all` is all time: no `from`, `win` gone", () => {
    mount("?categories=Dining&win=all", CARRIED_SEARCH);
    expect(loc().search).toBe("?categories=Dining");
  });
  it("an unknown `win` is ignored with ONE warning: the entry reads as if it had none (carried: all time; fresh: the default)", () => {
    mount("?categories=Dining&win=bogus", CARRIED_SEARCH);
    expect(loc().search).toBe("?categories=Dining");
    expect(warn.mock.calls.filter((c: unknown[]) => String(c[0]).includes("?win=bogus"))).toHaveLength(1);
    cleanup();
    mount("?win=bogus");
    expect(loc().sp.get("from"), "a fresh visit with a junk window still gets the default").toBe(DEFAULT_FROM);
    expect(loc().sp.has("win")).toBe(false);
    expect(warn.mock.calls.filter((c: unknown[]) => String(c[0]).includes("?win=bogus")), "still one").toHaveLength(1);
  });
  it("explicit dates beside a `win` win: the window request is dropped, the dates stay", () => {
    mount("?from=2025-11-03&win=6m", CARRIED_SEARCH);
    expect(loc().search).toBe("?from=2025-11-03");
  });
  it("a hand-typed ?win= on a fresh entry is honoured instead of the six-month default", () => {
    mount("?win=12m");
    expect(loc().sp.get("from")).toBe(presetRange("12m", MAX_DATE).from);
    expect(loc().sp.get("from")).not.toBe(DEFAULT_FROM);
  });
  it("a saved view opened from the Views menu while the tab is already mounted resolves too", () => {
    mount("?q=taco", CARRIED_SEARCH);
    act(() => go("/spending?categories=Dining&win=3m", { state: CARRIED_SEARCH }));
    expect(loc().sp.get("from")).toBe(presetRange("3m", MAX_DATE).from);
    expect(loc().sp.get("categories")).toBe("Dining");
    expect(loc().sp.has("win")).toBe(false);
  });
  it("the resolved window parses as a normal filter set", () => {
    mount("?categories=Dining&win=6m", CARRIED_SEARCH);
    expect(parseFilters(new URLSearchParams(loc().search))).toMatchObject({ categories: ["Dining"], from: DEFAULT_FROM });
  });
});

// The Save view button is for something the user chose: a bare fresh visit has only the page's own window on.
describe("withoutAutoWindow: what is left when the page's own window is taken out", () => {
  const dflt = { ...EMPTY_FILTERS, categories: ["Dining"], from: DEFAULT_FROM };
  it("drops the automatic window - the six-month `from`, no `to` - and nothing else", () => {
    expect(withoutAutoWindow(dflt, MAX_DATE)).toEqual({ ...EMPTY_FILTERS, categories: ["Dining"], from: undefined });
    expect(isAutoWindow(DEFAULT_FROM, undefined, MAX_DATE)).toBe(true);
  });
  it("keeps a window the user picked: another `from`, or a `to`", () => {
    const own = { ...dflt, from: "2025-11-03" };
    expect(withoutAutoWindow(own, MAX_DATE)).toBe(own);
    const bounded = { ...dflt, to: "2026-08-31" };
    expect(withoutAutoWindow(bounded, MAX_DATE)).toBe(bounded);
  });
  it("leaves a filter set with no window (All) exactly as it was", () => {
    const all = { ...EMPTY_FILTERS };
    expect(withoutAutoWindow(all, MAX_DATE)).toBe(all);
  });
});

// A household with no transaction yet has no newest date: no window can be resolved, so nothing is written from one and
// nothing is saved as one - a link or a saved query carries the dates it was given, or none.
describe("with no transaction yet", () => {
  let warn: ReturnType<typeof vi.spyOn>;
  beforeEach(() => { __resetFilterWarnings(); warn = vi.spyOn(console, "warn").mockImplementation(() => {}); data.end = null; });
  afterEach(() => { warn.mockRestore(); });

  it("a fresh visit gets no window: the URL stays bare", () => {
    mount("");
    expect(loc().search).toBe("");
  });
  it("a `win` request is removed and resolves to no date", () => {
    mount("?categories=Dining&win=6m", CARRIED_SEARCH);
    expect(loc().search).toBe("?categories=Dining");
  });
  it("a `win=all` request, which needs no anchor date, is removed and is the empty window", () => {
    mount("?categories=Dining&win=all", CARRIED_SEARCH);
    expect(loc().search).toBe("?categories=Dining");
  });
  it("no window is the automatic one or a rolling preset, so a save keeps the user's dates or none", () => {
    const f = { ...EMPTY_FILTERS, categories: ["Dining"], from: "2026-01-05" };
    expect(isAutoWindow("2026-01-05", undefined, null)).toBe(false);
    expect(rollingPreset("2026-01-05", undefined, null)).toBeNull();
    expect(withoutAutoWindow(f, null)).toBe(f);
    expect(savedFilterQuery(f, null)).toBe("categories=Dining&from=2026-01-05");
    expect(savedFilterQuery({ ...EMPTY_FILTERS, categories: ["Dining"] }, null)).toBe("categories=Dining");
    expect(savedQuery("categories=Dining&from=2026-01-05", null)).toBe("categories=Dining&from=2026-01-05");
    expect(savedQuery("categories=Dining", null)).toBe("categories=Dining");
  });
});

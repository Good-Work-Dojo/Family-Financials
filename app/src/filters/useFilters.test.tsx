// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useFilters } from "./useFilters";
import { useSort } from "./useSort";

let api: ReturnType<typeof useFilters>;
function Probe() {
  api = useFilters();
  const { search, state } = useLocation();
  return <output data-testid="loc">{search}|{JSON.stringify(state)}</output>;
}
const at = (search: string) => render(<MemoryRouter initialEntries={[`/spending${search}`]}><Probe /></MemoryRouter>);
afterEach(cleanup);

describe("useFilters writes", () => {
  it("update keeps non-filter params and marks the entry as deliberate (CARRIED_SEARCH)", () => {
    at("?categories=A&sort=amount:asc&snap=2026-01-01");
    act(() => api[1]({ vendors: ["V"] }));
    const [search, state] = screen.getByTestId("loc").textContent!.split("|");
    const sp = new URLSearchParams(search);
    expect(sp.get("vendors")).toBe("V");
    expect(sp.get("categories")).toBe("A");
    expect(sp.get("sort")).toBe("amount:asc");
    expect(sp.get("snap")).toBe("2026-01-01");
    expect(JSON.parse(state)).toEqual({ carriedSearch: true });
  });
  it("clear leaves the non-filter params and the marker, so Back onto a cleared tab is not a fresh visit", () => {
    at("?categories=A&from=2026-01-01&sort=amount:asc");
    act(() => api[2]());
    const [search, state] = screen.getByTestId("loc").textContent!.split("|");
    expect(search).toBe("?sort=amount%3Aasc");
    expect(JSON.parse(state)).toEqual({ carriedSearch: true });
  });
  it("clearing a query that has only filter keys leaves an empty search and still the marker", () => {
    at("?categories=A&from=2026-01-01");
    act(() => api[2]());
    const [search, state] = screen.getByTestId("loc").textContent!.split("|");
    expect(search).toBe("");
    expect(JSON.parse(state)).toEqual({ carriedSearch: true });
  });
});

let sortApi: ReturnType<typeof useSort>;
let filtersApi: ReturnType<typeof useFilters>;
const IDS = ["date", "vendor", "amount"] as const;
function SortProbe() {
  sortApi = useSort(IDS);
  filtersApi = useFilters();
  const { search, state } = useLocation();
  return <output data-testid="loc">{search}|{JSON.stringify(state)}</output>;
}
const atSort = (search: string) =>
  render(<MemoryRouter initialEntries={[`/spending/transactions${search}`]}><SortProbe /></MemoryRouter>);

describe("useSort writes", () => {
  it("setSort puts the sort in ?sort=, keeps the filters and marks the entry as deliberate", () => {
    atSort("?categories=A&snap=2026-01-01");
    act(() => sortApi[1]([{ id: "amount", desc: false }]));
    const [search, state] = screen.getByTestId("loc").textContent!.split("|");
    const sp = new URLSearchParams(search);
    expect(sp.get("sort")).toBe("amount:asc");
    expect(sp.get("categories")).toBe("A");
    expect(sp.get("snap")).toBe("2026-01-01");
    expect(JSON.parse(state)).toEqual({ carriedSearch: true });
    expect(sortApi[0]).toEqual([{ id: "amount", desc: false }]);
  });
  it("sorting back to the default drops the key; a filter edit keeps a non-default sort", () => {
    atSort("?sort=vendor:desc&q=x");
    act(() => filtersApi[1]({ q: "y" }));
    expect(new URLSearchParams(screen.getByTestId("loc").textContent!.split("|")[0]).get("sort")).toBe("vendor:desc");
    act(() => sortApi[1]([{ id: "date", desc: true }]));
    expect(screen.getByTestId("loc").textContent!.split("|")[0]).toBe("?q=y");
  });
  it("a stale ?sort= warns once while filters are edited around it, and the register falls back to the default", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      atSort("?sort=hook-gone:asc");
      expect(sortApi[0]).toEqual([{ id: "date", desc: true }]);
      act(() => filtersApi[1]({ q: "a" }));
      act(() => filtersApi[1]({ q: "ab" }));
      act(() => filtersApi[1]({ vendors: ["V"] }));
      expect(new URLSearchParams(screen.getByTestId("loc").textContent!.split("|")[0]).get("sort"),
        "the bad value is left for the user, not rewritten").toBe("hook-gone:asc");
      expect(warn).toHaveBeenCalledTimes(1);
    } finally {
      warn.mockRestore();
    }
  });
});

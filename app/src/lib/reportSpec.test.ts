// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { FILTER_PARAM_KEYS } from "../filters/filters";
import { DEFAULT_SPEC, LINK_BASELINE, parseReportSpec, REPORT_KEYS, searchFromSpec, specFromSearch } from "./reportSpec";

describe("ReportSpec", () => {
  it("parses a full valid spec and rejects any unknown enum value or a non-filter key in filters", () => {
    const ok = { filters: "categories=Dining", groupBy: "vendor", interval: "quarter", measure: "net", chart: "line" };
    expect(parseReportSpec(ok)).toEqual(ok);
    expect(parseReportSpec({ ...ok, groupBy: "merchant" })).toBeNull();
    expect(parseReportSpec({ ...ok, interval: "fortnight" })).toBeNull();
    expect(parseReportSpec({ ...ok, filters: "bogus=1" })).toBeNull();
    expect(parseReportSpec({ ...ok, filters: "categories=Dining&win=6m" })).toEqual({ ...ok, filters: "categories=Dining&win=6m" });   // a rolling window is allowed
    expect(parseReportSpec({ ...ok, filters: 7 })).toBeNull();
    expect(parseReportSpec(null)).toBeNull();
    expect(parseReportSpec("spend")).toBeNull();
  });
  it("round-trips through the URL: filter keys become spec.filters, a key absent or unknown beside others reads the link baseline", () => {
    const sp = new URLSearchParams("categories=Dining&from=2026-01-01&groupBy=account&interval=nope&chart=line&sort=amount:asc");
    const spec = specFromSearch(sp);
    expect(spec).toEqual({ filters: "categories=Dining&from=2026-01-01", groupBy: "account", interval: "month", measure: "spend", chart: "line" });
    expect(searchFromSpec(spec).toString()).toBe("categories=Dining&from=2026-01-01&groupBy=account&interval=month&measure=spend&chart=line");
  });
  it("the default report is spending by vendor, per month, as a table - not Explore's category-by-month chart", () => {
    expect(DEFAULT_SPEC).toEqual({ filters: "", groupBy: "vendor", interval: "month", measure: "spend", chart: "table" });
  });
  it("a link naming no report key is the default report; one naming SOME reads the rest as category, month, spending, stacked", () => {
    expect(specFromSearch(new URLSearchParams(""))).toEqual(DEFAULT_SPEC);
    expect(specFromSearch(new URLSearchParams("from=2026-01-01&categories=Dining")), "filters alone are no report key")
      .toEqual({ ...DEFAULT_SPEC, filters: "from=2026-01-01&categories=Dining" });
    expect(LINK_BASELINE).toEqual({ groupBy: "category", interval: "month", measure: "spend", chart: "stacked" });
    // an older bookmark of a quarterly report keeps opening spending by category per quarter, stacked
    expect(specFromSearch(new URLSearchParams("interval=quarter")))
      .toEqual({ filters: "", groupBy: "category", interval: "quarter", measure: "spend", chart: "stacked" });
    expect(specFromSearch(new URLSearchParams("groupBy=vendor")))
      .toEqual({ filters: "", groupBy: "vendor", interval: "month", measure: "spend", chart: "stacked" });
    expect(specFromSearch(new URLSearchParams("chart=bogus")), "an unknown value still names the key")
      .toEqual({ filters: "", ...LINK_BASELINE });
  });
  it("a saved report opens as itself whatever the default is: every spec, category by month included, survives the URL", () => {
    const specs = [
      { filters: "", groupBy: "category", interval: "month", measure: "spend", chart: "stacked" },
      { filters: "categories=Dining&from=2026-01-01", groupBy: "vendor", interval: "month", measure: "spend", chart: "stacked" },
      { filters: "categories=Dining", groupBy: "vendor", interval: "quarter", measure: "count", chart: "table" },
      { ...DEFAULT_SPEC },
    ] as const;
    for (const s of specs) expect(specFromSearch(searchFromSpec(s)), JSON.stringify(s)).toEqual(s);
  });
  it("every field of a stored spec is checked, not just the enums: a missing key or a non-string enum is refused", () => {
    const ok = { filters: "", groupBy: "category", interval: "week", measure: "count", chart: "table" };
    expect(parseReportSpec(ok)).toEqual(ok);
    for (const k of ["filters", "groupBy", "interval", "measure", "chart"]) {
      const { [k]: _dropped, ...rest } = ok as Record<string, string>;
      expect(parseReportSpec(rest), `no ${k}`).toBeNull();
      expect(parseReportSpec({ ...ok, [k]: 3 }), `${k} a number`).toBeNull();
    }
    expect(parseReportSpec({ ...ok, measure: "Spend" }), "enums are case-sensitive").toBeNull();
  });
  it("a stored spec drops keys it does not know (no payload rides along), and a report key inside filters is not a filter key", () => {
    const parsed = parseReportSpec({ filters: "q=x", groupBy: "owner", interval: "year", measure: "income", chart: "stacked", extra: "<script>" });
    expect(Object.keys(parsed!).sort()).toEqual(["chart", "filters", "groupBy", "interval", "measure"]);
    expect(parseReportSpec({ ...DEFAULT_SPEC, filters: "groupBy=vendor" }), "a report key smuggled into the filters").toBeNull();
    expect(parseReportSpec({ ...DEFAULT_SPEC, filters: "sort=amount:asc" }), "sort is a page key, not a filter").toBeNull();
  });
  it("a URL that names every filter key keeps them all, in URL order, and none of the report keys leak into filters", () => {
    const q = FILTER_PARAM_KEYS.map((k) => `${k}=1`).join("&");
    const spec = specFromSearch(new URLSearchParams(`${q}&groupBy=owner&measure=count&interval=year&chart=line`));
    expect(spec.filters).toBe(q);
    expect(new URLSearchParams(spec.filters).has("groupBy")).toBe(false);
    expect(parseReportSpec(spec)).toEqual(spec);                       // what the URL yields is always a storable spec
  });
  it("searchFromSpec writes all four report keys, the default's included, and the URL reads back to the same spec", () => {
    for (const k of REPORT_KEYS) expect(searchFromSpec(DEFAULT_SPEC).get(k)).toBe(DEFAULT_SPEC[k]);
    expect(searchFromSpec(DEFAULT_SPEC).toString()).toBe("groupBy=vendor&interval=month&measure=spend&chart=table");
    const spec = { filters: "from=2026-01-01&q=taco", groupBy: "earner", interval: "week", measure: "net", chart: "line" } as const;
    const sp = searchFromSpec(spec);
    expect(sp.toString()).toBe("from=2026-01-01&q=taco&groupBy=earner&interval=week&measure=net&chart=line");
    expect(specFromSearch(sp)).toEqual(spec);
  });
});

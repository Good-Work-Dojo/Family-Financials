// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { FILTER_PARAM_KEYS, SAVED_QUERY_KEYS } from "../filters/filters";

export const GROUP_BYS = ["category", "subcategory", "vendor", "account", "owner", "earner"] as const;
export const INTERVALS = ["week", "month", "quarter", "year"] as const;
export const MEASURES = ["spend", "income", "net", "count"] as const;
export const CHARTS = ["stacked", "line", "table"] as const;
export type GroupBy = (typeof GROUP_BYS)[number];
export type Interval = (typeof INTERVALS)[number];
export type Measure = (typeof MEASURES)[number];
export type ChartKind = (typeof CHARTS)[number];

/** The words each token is shown as. The tokens are URL and storage values (`?groupBy=earner`) and must not change; what a
 *  person reads is these - the pickers, the card title, the saved-report lists - so "earner" and "net" never surface raw. */
export const GROUP_BY_LABELS: Record<GroupBy, string> = {
  category: "category", subcategory: "subcategory", vendor: "vendor", account: "account", owner: "owner", earner: "income earner",
};
export const INTERVAL_LABELS: Record<Interval, string> = { week: "week", month: "month", quarter: "quarter", year: "year" };
export const MEASURE_LABELS: Record<Measure, string> = {
  spend: "spending", income: "income", net: "net (income − spend)", count: "transactions (count)",
};
export const CHART_LABELS: Record<ChartKind, string> = { stacked: "stacked bars", line: "lines", table: "table" };

/** "spending by category, per month": a report named in words, for its title, its view-data table and every saved list */
export function describeReport(s: Pick<ReportSpec, "measure" | "groupBy" | "interval">): string {
  return `${MEASURE_LABELS[s.measure]} by ${GROUP_BY_LABELS[s.groupBy]}, per ${INTERVAL_LABELS[s.interval]}`;
}

/** `filters` is the FilterBar's serialized query (parseFilters reads it), so one validator covers
 *  saved views and saved reports; the four report keys are the Reports tab's page-scoped URL keys */
export interface ReportSpec { filters: string; groupBy: GroupBy; interval: Interval; measure: Measure; chart: ChartKind }
/** The report a bare Reports URL (one naming no report key) shows: spending by vendor, per month, as a table - who the
 *  money goes to, month by month, which Explore does not show (its first chart is spending by category per month, and
 *  its top-vendors chart is one total per vendor). Months, because the tab opens on the last six months: a row per
 *  month reads each regular vendor across the window and leaves a one-off payment one cell, where quarters gave two or
 *  three mostly partial rows. A table, because a dozen vendor bands stacked in one bar do not read. */
export const DEFAULT_SPEC: ReportSpec = { filters: "", groupBy: "vendor", interval: "month", measure: "spend", chart: "table" };
/** What a link that names SOME report keys means by the ones it leaves out: spending by category, per month, as stacked
 *  bars - what such a link has always meant, so a bookmark or an older saved link keeps opening the report it named.
 *  Every link the app writes names all four keys (searchFromSpec), so this only ever reads a hand-made or older one. */
export const LINK_BASELINE: Omit<ReportSpec, "filters"> = { groupBy: "category", interval: "month", measure: "spend", chart: "stacked" };
export const REPORT_KEYS = ["groupBy", "interval", "measure", "chart"] as const;

const oneOf = <T extends readonly string[]>(xs: T, v: unknown): v is T[number] =>
  typeof v === "string" && (xs as readonly string[]).includes(v);

/** a stored spec, defensively: any unknown enum value or a key that is neither a filter nor the rolling window makes it null (dropped with a warning by the caller) */
export function parseReportSpec(raw: unknown): ReportSpec | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.filters !== "string" || !oneOf(GROUP_BYS, r.groupBy) || !oneOf(INTERVALS, r.interval)
      || !oneOf(MEASURES, r.measure) || !oneOf(CHARTS, r.chart)) return null;
  if ([...new URLSearchParams(r.filters).keys()].some((k) => !SAVED_QUERY_KEYS.includes(k))) return null;
  return { filters: r.filters, groupBy: r.groupBy, interval: r.interval, measure: r.measure, chart: r.chart };
}

/** the Reports URL -> spec: filter keys become `filters`. A URL naming no report key is the default report; one naming
 *  some reads each absent or unknown key from LINK_BASELINE (the URL is the user's; never a crash) */
export function specFromSearch(sp: URLSearchParams): ReportSpec {
  const f = new URLSearchParams();
  for (const [k, v] of sp) if (FILTER_PARAM_KEYS.includes(k)) f.set(k, v);
  const base = REPORT_KEYS.some((k) => sp.has(k)) ? LINK_BASELINE : DEFAULT_SPEC;
  const pick = <T extends readonly string[]>(xs: T, k: string, d: T[number]): T[number] => {
    const v = sp.get(k);
    return oneOf(xs, v) ? v : d;
  };
  return { filters: f.toString(), groupBy: pick(GROUP_BYS, "groupBy", base.groupBy),
    interval: pick(INTERVALS, "interval", base.interval), measure: pick(MEASURES, "measure", base.measure),
    chart: pick(CHARTS, "chart", base.chart) };
}

/** the Reports URL of a spec: its filters first, then all four report keys - a link never leans on a default, so it
 *  opens the same report whatever the default is */
export function searchFromSpec(spec: ReportSpec): URLSearchParams {
  const sp = new URLSearchParams(spec.filters);
  for (const k of REPORT_KEYS) sp.set(k, spec[k]);
  return sp;
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { AppData, Transaction } from "../data/types";
import { applyFilters, parseFilters } from "../filters/filters";
import { countedRows, coversFromStart, daysInMonth, isIncome, isSpend, nextMonth } from "./agg";
import { NO_CATEGORY } from "./categories";
import { categoryTable, type ChartTable } from "./chartTable";
import { baseGrid, catAxis, CHART_MUTED, CHART_TEXT, moneyAxis, wrapLegend } from "./echarts";
import { fmtCompact, fmtDate, fmtMonth, fmtMonthAxis, fmtUSD0 } from "./format";
import type { GroupBy, Interval, Measure, ReportSpec } from "./reportSpec";
import { purchaseKey } from "./splits";
import { accountColor, CATEGORY_PALETTE, categoryColor } from "./tokens";

export const MAX_GROUPS = 12;

/** the bucket a date falls in: week = the ISO week's Monday (YYYY-MM-DD), month YYYY-MM, quarter YYYY-Qn, year YYYY */
export function periodOf(date: string, interval: Interval): string {
  if (interval === "year") return date.slice(0, 4);
  if (interval === "month") return date.slice(0, 7);
  if (interval === "quarter") return `${date.slice(0, 4)}-Q${Math.floor((Number(date.slice(5, 7)) - 1) / 3) + 1}`;
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));      // back to Monday
  return d.toISOString().slice(0, 10);
}

export function nextPeriod(p: string, interval: Interval): string {
  if (interval === "year") return String(Number(p) + 1);
  if (interval === "month") return nextMonth(p);
  if (interval === "quarter") {
    const y = Number(p.slice(0, 4)), q = Number(p.slice(6));
    return q === 4 ? `${y + 1}-Q1` : `${y}-Q${q + 1}`;
  }
  const d = new Date(`${p}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 7);
  return d.toISOString().slice(0, 10);
}

/** contiguous periods from first to last inclusive (an axis never skips a bucket); bounded */
export function periodsBetween(first: string, last: string, interval: Interval): string[] {
  const out: string[] = [];
  for (let p = first; p <= last && out.length < 2600; p = nextPeriod(p, interval)) out.push(p);
  return out;
}

/** the first day of a period, ISO */
export function periodStart(p: string, interval: Interval): string {
  switch (interval) {
    case "week": return p;
    case "month": return `${p}-01`;
    case "quarter": return `${p.slice(0, 4)}-${String((Number(p.slice(6)) - 1) * 3 + 1).padStart(2, "0")}-01`;
    case "year": return `${p}-01-01`;
  }
}

/** the last day of a period, ISO: the day before the next one starts */
export function periodEnd(p: string, interval: Interval): string {
  const d = new Date(`${periodStart(nextPeriod(p, interval), interval)}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
}

function groupOf(t: Transaction, g: GroupBy): string {
  switch (g) {
    case "category": return t.category ?? NO_CATEGORY;
    case "subcategory": return `${t.category ?? NO_CATEGORY} / ${t.subcategory ?? "—"}`;
    case "vendor": return t.vendor ?? "—";
    case "account": return t.account_name;
    case "owner": return t.owner;
    case "earner": return t.earner ?? "—";
  }
}

/** spend = −amount over spend rows (refunds reduce it, like totalSpend); income over income rows;
 *  net = income − spend, signed; count = every purchase the filters pass (runReport counts a split
 *  purchase once per cell). null = this row does not count. */
function measureOf(t: Transaction, m: Measure): number | null {
  switch (m) {
    case "spend": return isSpend(t) ? -t.amount : null;
    case "income": return isIncome(t) ? t.amount : null;
    case "net": return isSpend(t) || isIncome(t) ? t.amount : null;
    case "count": return 1;
  }
}

/** what each measure counts, in the words the Reports tab prints under the picker (one place, beside the math) */
export const MEASURE_DEFINITIONS: Record<Measure, string> = {
  spend: "Spend = purchases and fees, with refunds netted: a refund reduces the figure it belongs to. Income, transfers and investments are not spend.",
  income: "Income = income, dividends and interest, as positive dollars.",
  net: "Net = income − spend, signed: a negative figure means spending outran income in that period.",
  count: "Count = the transactions the filters pass, whatever their type — a purchase split by a vendor rule counts once in each group it touches, never once per part; a transaction dated into a month not begun is not counted yet.",
};

/** The rows a report reads — ONE function, the single switch between the two views (lib/splits.ts). A vendor
 *  group-by reads the vendor view: one row per purchase, a split purchase once at its full amount —
 *  unless the filters judge something only a PART can answer: a category (a parent's is display-only),
 *  a tag (a tags-only answer can tag one part), an amount bound (a part is a fraction of the parent) or
 *  the review flag (a part's can differ). Then it reads the category view and `runReport` counts
 *  purchases by `purchaseKey`. Every other group-by reads the category view. */
export function reportInput(data: Pick<AppData, "transactions" | "purchases">, spec: ReportSpec): readonly Transaction[] {
  if (spec.groupBy !== "vendor") return data.transactions;
  const f = parseFilters(new URLSearchParams(spec.filters));
  const partLevel = f.categories.length > 0 || f.tags.length > 0 || f.amountMin !== undefined
    || f.amountMax !== undefined || f.needsReviewOnly;
  return partLevel ? data.transactions : data.purchases;
}

export interface ReportResult {
  periods: string[];
  groups: string[];                             // top groups by |total|, then "Other (n)"
  cells: Map<string, Map<string, number>>;      // group -> period -> value; a cell nothing fed is absent (read it with cellValue)
  totals: Map<string, number>;
  /** the name of the roll-up group when groups past the cap were merged (it is also the last of `groups`) */
  other: string | null;
  /** periods the window covers only in part: the first when the From date falls inside it after its start, or the
   *  data starts inside it (after its first days, for a month or longer: lib/agg.ts coversFromStart), the last when
   *  the data (or the To date) ends inside it or
   *  it reaches the end of the month in progress.
   *  Their numbers are real but incomplete, so they are labelled. */
  partial: ReadonlySet<string>;
  /** periods in which the rows handed to runReport hold no transaction at all: a gap in the feeds, so a
   *  cell there is unknown (a dash), not a real zero */
  silent: ReadonlySet<string>;
}

/** The report over `input` (`reportInput`'s rows). `inProgress` is the month in progress (lib/agg.ts `monthInProgress`;
 *  null reads every row): a row dated in a month after it has not begun is in no period and no total, and a last
 *  period that reaches the end of the month in progress is partial, whatever day the data ends on. */
export function runReport(input: readonly Transaction[], spec: ReportSpec, maxGroups = MAX_GROUPS,
                          inProgress: string | null = null): ReportResult {
  const txns = countedRows(input, inProgress);
  const f = parseFilters(new URLSearchParams(spec.filters));
  const rows = applyFilters(txns, f);
  // sums run in integer cents, so 0.1 + 0.2 is 0.30 and the CSV never carries float noise; dollars again at the end
  const raw = new Map<string, Map<string, number>>();
  const counted = new Map<string, Set<string>>();
  const totals = new Map<string, number>();
  // the first and last periods a counted row falls in; null until one does
  let first: string | null = null, last: string | null = null;
  for (const t of rows) {
    const v = measureOf(t, spec.measure);
    if (v === null) continue;
    const p = periodOf(t.date, spec.interval), g = groupOf(t, spec.groupBy);
    if (first === null || p < first) first = p;
    if (last === null || p > last) last = p;
    if (spec.measure === "count") {
      // count purchases, never parts: a split purchase counts once per (group, period) cell
      const cell = `${g}\u0000${p}`;
      let seen = counted.get(cell);
      if (!seen) counted.set(cell, (seen = new Set()));
      if (seen.has(purchaseKey(t))) continue;
      seen.add(purchaseKey(t));
    }
    const c = Math.round(v * 100);
    let m = raw.get(g);
    if (!m) raw.set(g, (m = new Map()));
    m.set(p, (m.get(p) ?? 0) + c);
    totals.set(g, (totals.get(g) ?? 0) + c);
  }
  const dollars = (m: Map<string, number>) => new Map([...m].map(([k, c]) => [k, c / 100] as [string, number]));
  // by |total|, a tie broken by name: which groups make the top 12 must not depend on the order the rows came in
  const ranked = [...totals.entries()]
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]) || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0)).map(([g]) => g);
  const top = ranked.slice(0, maxGroups), rest = ranked.slice(maxGroups);
  const cells = new Map(top.map((g) => [g, dollars(raw.get(g)!)]));
  const outTotals = new Map(top.map((g) => [g, totals.get(g)! / 100]));
  let other: string | null = null;
  if (rest.length) {
    other = `Other (${rest.length})`;
    const merged = new Map<string, number>();
    let total = 0;
    if (spec.measure === "count") {
      // a split purchase whose parts fell in two rolled-up groups is ONE purchase in "Other": merge the groups'
      // purchase sets period by period (and for the total), instead of adding their counts, which counted it twice
      const perPeriod = new Map<string, Set<string>>();
      const everyone = new Set<string>();
      for (const g of rest) {
        for (const p of raw.get(g)!.keys()) {
          let into = perPeriod.get(p);
          if (!into) perPeriod.set(p, (into = new Set()));
          for (const key of counted.get(`${g}\u0000${p}`)!) { into.add(key); everyone.add(key); }
        }
      }
      for (const [p, keys] of perPeriod) merged.set(p, keys.size * 100);
      total = everyone.size * 100;
    } else {
      for (const g of rest) {
        for (const [p, v] of raw.get(g)!) merged.set(p, (merged.get(p) ?? 0) + v);
        total += totals.get(g)!;
      }
    }
    cells.set(other, dollars(merged));
    outTotals.set(other, total / 100);
  }
  const periods = first !== null && last !== null ? periodsBetween(first, last, spec.interval) : [];
  // Completeness is judged against the whole input, not the filtered rows: a filter that matches little
  // does not make a month "unreported", and the newest data date is the household's, not the filter's.
  const alive = new Set<string>();
  // the input's first and last dates; null with no row, when there are no periods to judge either
  let data: { start: string; end: string } | null = null;
  for (const t of txns) {
    alive.add(periodOf(t.date, spec.interval));
    if (data === null) data = { start: t.date, end: t.date };
    else if (t.date < data.start) data.start = t.date;
    else if (t.date > data.end) data.end = t.date;
  }
  const partial = new Set<string>();
  if (first !== null && last !== null && data !== null) {
    const { start: dataStart, end: dataEnd } = data;
    // the window the rows can really cover: the From / To dates, cut to where the data starts and ends
    const picked = !!f.from && f.from > dataStart;
    const windowStart = picked ? f.from! : dataStart;
    const windowEnd = f.to && f.to < dataEnd ? f.to : dataEnd;
    // the one rule (lib/agg.ts): a From the person picked, or a week, is judged strictly; a month or longer that
    // begins on the data's own first day is covered when the data begins by its COVERED_FROM_DAY-th day
    if (!coversFromStart(periodStart(first, spec.interval), windowStart, { interval: spec.interval, picked })) {
      partial.add(first);
    }
    // the month in progress is never whole, even when the data reaches its last day
    const inProgressEnd = inProgress === null ? null : `${inProgress}-${String(daysInMonth(inProgress)).padStart(2, "0")}`;
    if (periodEnd(last, spec.interval) > windowEnd
      || (inProgressEnd !== null && periodEnd(last, spec.interval) >= inProgressEnd)) partial.add(last);
  }
  return {
    periods, groups: [...cells.keys()], cells, totals: outTotals, other,
    partial, silent: new Set(periods.filter((p) => !alive.has(p))),
  };
}

/** one cell: the measure over the group's rows in the period. A cell nothing fed is a real 0 when the data
 *  was alive that period (transactions exist, none matched), and null — unknown, drawn as a gap and read
 *  as a dash — when the data holds no transaction at all in it. */
export function cellValue(res: ReportResult, group: string, period: string): number | null {
  return res.cells.get(group)?.get(period) ?? (res.silent.has(period) ? null : 0);
}

/** the report as a table, its periods named as the page names them in prose (`periodLabel`: "May 2026", a partial one
 *  starred, as the line under the report stars it), never the raw key */
export function reportTable(res: ReportResult, measure: Measure, interval: Interval): ChartTable {
  const t = categoryTable("Period", res.periods.map((p) => periodLabel(p, interval, res.partial.has(p))),
    res.groups.map((g) => ({ name: g, data: res.periods.map((p) => cellValue(res, g, p)) })));
  return measure === "count" ? { ...t, format: (v) => v.toLocaleString("en-US") } : t;
}

/** the text of a period: a month as "Jan 2026" (or "Jan ’26" on the chart's axis, `axis`), a week by its Monday as a
 *  date ("Jan 5, 2026"; the axis keeps the short key), a quarter or a year as its own key, and a partial period starred
 *  ("Apr 2026*") */
export function periodLabel(p: string, interval: Interval, partial: boolean, axis = false): string {
  const text = interval === "month" ? (axis ? fmtMonthAxis(p) : fmtMonth(p)) : interval === "week" && !axis ? fmtDate(p) : p;
  return text + (partial ? "*" : "");
}

/** The report chart's colour for a group: an account (grouped by account) keeps its own colour, its place in the
 *  household's account list (tokens.ts accountColor: the same as on every balance chart), found by the name its rows
 *  carry; an account the list lacks (one since removed from accounts.yaml) takes the palette's next colours after the
 *  listed ones, by name among such `groups`, never the grey of the "Other" roll-up. Every other grouping takes the
 *  first-asked palette (categoryColor). */
export function groupColorOf(spec: Pick<ReportSpec, "groupBy">, accounts: readonly { id: string; name: string }[],
                             groups: readonly string[]): (group: string) => string {
  if (spec.groupBy !== "account") return categoryColor;
  const ids = accounts.map((a) => a.id);
  const idOf = new Map<string, string>();
  for (const a of accounts) if (!idOf.has(a.name)) idOf.set(a.name, a.id);
  const unlisted = [...new Set(groups.filter((g) => !idOf.has(g)))].sort();
  return (group) => {
    const id = idOf.get(group);
    if (id !== undefined) return accountColor(id, ids);
    const k = unlisted.indexOf(group);
    return CATEGORY_PALETTE[(ids.length + Math.max(0, k)) % CATEGORY_PALETTE.length];
  };
}

export function reportOption(res: ReportResult, spec: ReportSpec, colorOf: (group: string) => string = categoryColor): object {
  const count = spec.measure === "count";
  const labels = res.periods.map((p) => periodLabel(p, spec.interval, res.partial.has(p), true));
  // a week's label is a whole ISO date (10 characters): centred under the last of many thin bands it would hang
  // past the plot and be clipped, so a week chart keeps a wider right gutter (the phone tuning keeps an author's
  // gutter wider than 16)
  const grid = spec.interval === "week" ? { ...baseGrid, right: 32 } : baseGrid;
  return {
    grid, legend: wrapLegend(),
    // a gap (a silent period) reaches the formatter as undefined: it reads "—", never a $0 the table does not show
    tooltip: { trigger: "axis", valueFormatter: (v: number | null | undefined) =>
      (v === null || v === undefined ? "—" : count ? v.toLocaleString("en-US") : fmtUSD0(v)) },
    xAxis: catAxis(labels),
    yAxis: count ? { type: "value", axisLabel: { color: CHART_TEXT } } : moneyAxis(fmtCompact),
    series: res.groups.map((g) => ({
      name: g, type: spec.chart === "line" ? "line" : "bar", stack: spec.chart === "stacked" ? "report" : undefined,
      color: g === res.other ? CHART_MUTED : colorOf(g), data: res.periods.map((p) => cellValue(res, g, p)),
    })),
  };
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useDeferredValue, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import EChart from "../components/EChart";
import FilterBar from "../components/FilterBar";
import { EmptyState, NotEnoughHistory, NoTransactions } from "../components/NoData";
import SplitNote from "../components/SplitNote";
import { Card, SegmentToggle, TableScroll } from "../components/ui";
import { useData } from "../data/DataContext";
import { monthBounds } from "../filters/dateRange";
import { applyFilters, spendingPath } from "../filters/filters";
import { useFilters } from "../filters/useFilters";
import { useSpendingDefaults } from "../filters/useSpendingDefaults";
import {
  countedRows, monthInProgress, monthlyByCategory, periodCompare, spanDays, vendorStats, WEEKDAY_MIN_DAYS, weekdaySpend,
  windowPartialMonths,
} from "../lib/agg";
import { calendarSpan, weeksSpanned } from "../lib/calendarSpan";
import { NO_CATEGORY } from "../lib/categories";
import { categoryTable } from "../lib/chartTable";
import { compareWindow } from "../lib/compareWindow";
import { baseGrid, catAxis, CHART_AXIS, CHART_MUTED, CHART_RAMP, CHART_SPLIT, CHART_SURFACE, CHART_TEXT, fitTileLabel, moneyAxis, moneyTooltip, moneyValue, monthAxis, wrapLegend, treemapLabelFit, treemapTileBox } from "../lib/echarts";
import { countOf, fmtCompact, fmtDate, fmtDelta, fmtMonth, fmtUSD0 } from "../lib/format";
import { categoryColor } from "../lib/tokens";
import { usePointerWords } from "../lib/pointerWords";
import { useIsMobile } from "../lib/useMediaQuery";
import VendorDrill from "./VendorDrill";

/** the breakdown treemap's series id: lib/echarts.ts records each tile's laid-out box under it */
const TREEMAP_ID = "explore-breakdown";
/** the treemap's breadcrumb height, and the strip at the chart's foot it sits in, under the tiles (8px between the two) */
const BREADCRUMB_HEIGHT = 22;
const TREEMAP_FOOT = BREADCRUMB_HEIGHT + 8;
/** a phone calendar's row of a week: a day's cell is this tall and about as wide (a seventh of the card), a finger's
 *  target */
const CALENDAR_TAP = 44;
/** the phone calendar's margins: the weekday labels above it, the month labels to its left, the scale under it */
const CALENDAR_PHONE = { top: 32, left: 36, right: 8, bottom: 56 };

/** what a chart card says in place of its chart when the selection holds no spending */
const NO_SPENDING = "No spending matches these dates and filters. Widen the dates or clear a filter.";

/** spending a chart can draw: a purchase or a fee that took money out (a refund alone is no bar, tile or day) */
const isOutflow = (t: { type: string; amount: number }) => t.amount < 0 && (t.type === "purchase" || t.type === "fee");

export default function Spending() {
  const { data, indexes } = useData();
  const [filters, update, clear] = useFilters();
  const [drillVendor, setDrillVendor] = useState<string | null>(null);
  const [breakdown, setBreakdown] = useState<"treemap" | "donut">("treemap");
  const [compareMode, setCompareMode] = useState<"mom" | "yoy">("mom");
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  // the cards' "click …" read "tap …" for a finger (lib/pointerWords.ts)
  const { click } = usePointerWords();

  useSpendingDefaults();

  // the charts follow the filters a beat behind the bar: a keystroke in the search box re-renders the bar at
  // once and the six charts only when React has time - once per pause in typing, not once per key
  const shown = useDeferredValue(filters);
  // the month in progress (lib/agg.ts monthInProgress) and the data's last day (never past the ingest day): every chart
  // below counts the filtered rows up to the month in progress - a row the bank dated into a month that has not begun
  // stays in the selection (the filter bar's count and the register it links to) and is charted nowhere until its month
  // begins
  const inProgress = useMemo(() => monthInProgress(data), [data]);
  const dataEnd = indexes.dataThrough;
  const selected = useMemo(
    () => applyFilters(data.transactions, shown),
    [data.transactions, shown]);
  const filtered = useMemo(() => countedRows(selected, inProgress), [selected, inProgress]);

  // ---- monthly stacked by category ----
  const monthly = useMemo(() => monthlyByCategory(filtered), [filtered]);
  // the months the From / To dates or the start or end of the data cover only in part, and the month in progress:
  // starred, as on Reports (with no transaction there is no data to cover a month in part)
  const partial = useMemo(
    () => windowPartialMonths(monthly.months,
      { dataStart: indexes.dataRange?.first ?? null, dataEnd, from: shown.from, to: shown.to }, inProgress),
    [monthly.months, indexes.dataRange, dataEnd, shown.from, shown.to, inProgress]);
  const stacked = useMemo(() => {
    // top categories get their own band; the tail rolls into "Other" so
    // nothing is silently dropped from the chart
    const TOP = 12;
    const top = monthly.categories.slice(0, TOP);
    const rest = monthly.categories.slice(TOP);
    const bands = top.map((c) => ({
      name: c, color: categoryColor(c),
      data: monthly.months.map((m) => monthly.data.get(m)?.get(c) ?? 0),
    }));
    if (rest.length) {
      bands.push({
        name: `Other (${rest.length} categories)`, color: CHART_MUTED,
        data: monthly.months.map((m) =>
          rest.reduce((s, c) => s + (monthly.data.get(m)?.get(c) ?? 0), 0)),
      });
    }
    const series: object[] = bands.map((b) => ({ ...b, type: "bar", stack: "spend" }));
    return {
      // the view-data table lists the same bands, the "Other" roll-up included
      table: { ...categoryTable("Month", monthly.months.map((m) => (partial.has(m) ? `${m} (partial)` : m)), bands),
        csvName: "spending-by-category.csv", label: "monthly spend by category" },
      option: {
        grid: baseGrid,
        legend: wrapLegend(),
        tooltip: moneyTooltip(),
        xAxis: monthAxis(monthly.months, partial),
        yAxis: moneyAxis(fmtCompact),
        series,
      },
    };
  }, [monthly, partial]);

  // ---- top vendors ----
  const vstats = useMemo(() => vendorStats(filtered), [filtered]);
  const topVendors = useMemo(() => vstats.slice(0, isMobile ? 10 : 20), [vstats, isMobile]);
  const vendorChart = useMemo(() => {
    const names = topVendors.map((v) => v.vendor);
    const totals = topVendors.map((v) => v.total);
    return {
      table: { ...categoryTable("Vendor", names, [{ name: "Spend", data: totals }]), csvName: "top-vendors.csv", label: "top vendors" },
      option: {
        grid: { ...baseGrid, left: 8 },
        tooltip: { valueFormatter: (v: number | null | undefined) => moneyValue(v) },
        xAxis: moneyAxis(fmtCompact),
        // interval 0: label every bar (ECharts otherwise drops every other category label when 20
        // rows are tighter than the font); the phone shows the top 10 so each row stays readable
        yAxis: { type: "category", inverse: true, data: names,
          axisLabel: { color: CHART_TEXT, width: 130, overflow: "truncate", interval: 0, fontSize: 10 } },
        series: [{ type: "bar", color: "#3b82f6", data: totals }],
      },
    };
  }, [topVendors]);

  // ---- treemap / donut breakdown ----
  // null with no spending to divide: the card says so instead of a white block or an empty ring
  const breakdownOption = useMemo(() => {
    const byCat = new Map<string, Map<string, number>>();
    for (const t of filtered) {
      if (!isOutflow(t)) continue;
      const c = t.category ?? NO_CATEGORY;
      let subs = byCat.get(c);
      if (!subs) byCat.set(c, (subs = new Map()));
      const s = t.subcategory ?? "—";
      subs.set(s, (subs.get(s) ?? 0) - t.amount);
    }
    if (byCat.size === 0) return null;
    if (breakdown === "treemap") {
      // A tile is labelled only when its WHOLE label fits: ECharts truncates the rest, and a phone's small tiles
      // read "T…" and "$4…" - noise. fitTileLabel measures the name and the value against the tile's real box
      // (recorded by lib/echarts.ts's layout stage under TREEMAP_ID): both lines, else the name, else nothing -
      // the tooltip names every tile. A cell under 3% of its level is never labelled (the level's size is the
      // parent's value in the tree path - the root's, for a category - else the whole total).
      const total = [...byCat.values()].reduce((s, subs) => s + [...subs.values()].reduce((a, b) => a + b, 0), 0);
      return {
        tooltip: { valueFormatter: (v: number | null | undefined) => moneyValue(v) },
        series: [{
          // leafDepth 1: show categories only; a click drills into its subcategories and the
          // breadcrumb returns (every subcategory at once crowded the labels into each other); the
          // series name labels the breadcrumb's root crumb, which is otherwise an empty box
          // drillDownIcon "": the label carries the ▶ itself (fitTileLabel), so an unlabelled tile shows no lone icon
          // the tiles fill the chart down to TREEMAP_FOOT above its foot, and the breadcrumb sits in that strip, under them:
          // ECharts' own box is 80% of the chart, centred, with the breadcrumb at the foot, which on a short (phone)
          // chart laid it over the bottom tiles. width and height null: the box is the chart less those edges
          // no animation: a treemap animates a new layout by its own 900ms (its series default, which a chart-wide
          // `animation: false` does not reach) while its labels change at once, so tiles kept the last window's sizes
          // under the new window's amounts (on arrival the page draws the whole history before the six-month default)
          animation: false,
          type: "treemap", id: TREEMAP_ID, name: "All categories", roam: false, leafDepth: 1,
          top: 0, left: 0, right: 0, bottom: TREEMAP_FOOT, width: null, height: null,
          breadcrumb: { show: true, left: "center", bottom: 0, height: BREADCRUMB_HEIGHT },
          drillDownIcon: "",
          label: {
            formatter: (p: any) => {
              const parent = p.treePathInfo?.[p.treePathInfo.length - 2]?.value ?? total;
              if (p.value / parent < 0.03) return "";
              const name = String(p.name);
              return fitTileLabel(treemapTileBox(TREEMAP_ID, p.dataIndex), [[name, fmtCompact(p.value)], [name]]);
            },
            // never cut: a label that does not fit the tile it is drawn on is hidden by treemapLabelFit
            overflow: "none",
          },
          labelLayout: treemapLabelFit,
          data: [...byCat.entries()].map(([name, subs]) => ({
            name, value: [...subs.values()].reduce((a, b) => a + b, 0),
            itemStyle: { color: categoryColor(name) },
            children: [...subs.entries()].map(([sn, sv]) => ({ name: sn, value: sv })),
          })),
        }],
      };
    }
    return {
      tooltip: { valueFormatter: (v: number | null | undefined) => moneyValue(v) },
      legend: { textStyle: { color: CHART_TEXT }, type: "scroll", bottom: 0 },
      series: [{
        type: "pie", radius: ["40%", "70%"],
        label: { color: CHART_TEXT, formatter: "{b} {d}%" },
        data: [...byCat.entries()].map(([name, subs]) => ({
          name, value: [...subs.values()].reduce((a, b) => a + b, 0),
          itemStyle: { color: categoryColor(name) },
        })),
      }],
    };
  }, [filtered, breakdown]);

  // ---- MoM / YoY compare ----
  // ends at the data's last COMPLETE day (the ingest day is still coming in, as on Home's "this month so far") or the
  // filter's To: a month cut short - the month in progress always - is set against the SAME days of the older month,
  // and the title says how many in plain words - never an in-progress month against a whole one (lib/compareWindow.ts).
  // With no transaction there is no end to compare from (null): the card says so
  const compare = useMemo(() => {
    const w = compareWindow({ dataThrough: dataEnd, generatedAt: data.manifest.generated_at, to: shown.to, mode: compareMode, inProgress });
    if (w === null) return null;
    const p = w.periods;
    // an older period that ends before the data's first day holds no data to set against: not a column of $0
    const first = indexes.dataRange?.first ?? null;
    return { rows: periodCompare(filtered, p.cur[0], p.cur[1], p.prev[0], p.prev[1]).slice(0, 12),
      cur: p.curMonth, prev: p.prevMonth, words: w.words, before: first !== null && p.prev[1] < first ? first : null };
  }, [filtered, shown.to, dataEnd, data.manifest.generated_at, compareMode, inProgress, indexes.dataRange]);

  // ---- weekday + calendar ----
  // with no outflow in the selection there is no weekday to average and no day to colour: the cards say so
  const hasOutflow = useMemo(() => filtered.some(isOutflow), [filtered]);
  // the weekday averages read the days the window and the data share: from the later of the From date and the first
  // data day to the earlier of the To date and the day the data reaches, each weekday over its own count of those days
  const firstDay = indexes.dataRange?.first ?? null;
  // the averages need four weeks of those days (WEEKDAY_MIN_DAYS): fewer, and the card says how many days there are
  const weekdayWindow = useMemo(() => {
    if (firstDay === null || dataEnd === null) return null;
    const from = shown.from && shown.from > firstDay ? shown.from : firstDay;
    const to = shown.to && shown.to < dataEnd ? shown.to : dataEnd;
    return { avg: weekdaySpend(filtered, from, to), days: spanDays(from, to) };
  }, [filtered, shown.from, shown.to, firstDay, dataEnd]);
  const weekday = weekdayWindow?.avg ?? null;
  const weekdayShort = weekdayWindow !== null && weekdayWindow.days < WEEKDAY_MIN_DAYS;
  const hasWeekday = !weekdayShort && weekday !== null && weekday.some((v) => v !== null && v > 0);
  const weekdayOption = useMemo(() => ({
    grid: baseGrid,
    tooltip: { valueFormatter: (v: number | null | undefined) => moneyValue(v) },
    xAxis: catAxis(["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]),
    yAxis: moneyAxis(fmtCompact),
    // a weekday the days do not reach has no bar (null), never a bar of 0
    series: [{ type: "bar", data: (weekday ?? []).map((v, i) => ({
      value: v, itemStyle: { color: i === 0 || i === 6 ? "#f59e0b" : "#3b82f6" } })) }],
  }), [weekday]);

  // A phone draws the calendar on its side - a column per weekday, a row per week - so a day is a CALENDAR_TAP-tall cell
  // about as wide (a finger's target, which a 14px cell of a year across was not), over three months, the newest
  const calendarMonths = isMobile ? 3 : 12;
  const calendar = useMemo(() => {
    const daily = new Map<string, number>();
    for (const t of filtered) {
      if (!isOutflow(t)) continue;
      daily.set(t.date, (daily.get(t.date) ?? 0) - t.amount);
    }
    // the calendar reads up to the window's To date, else the data's last day (with neither there is no range to draw),
    // and spans the months that hold spending back from the newest of them, at most `calendarMonths` (lib/calendarSpan.ts):
    // a short history is not drawn among months of empty days, and spending that stopped long ago is still drawn
    const end = shown.to ?? dataEnd;
    const span = end === null ? null : calendarSpan(daily.keys(), end, calendarMonths);
    if (span === null) return null;
    const vals = [...daily.values()].sort((a, b) => a - b);
    const p95 = vals[Math.floor(vals.length * 0.95)] ?? 100;
    return {
      span,
      option: {
        tooltip: { formatter: (p: any) => `${fmtDate(String(p.value[0]))}: ${fmtUSD0(p.value[1])}` },
        // the scale names the amounts its two ends stand for (the top one and anything above it: the ramp tops out at the
        // 95th-percentile day), through the formatter, so privacy mode masks them. Every colour along the ramp is at least
        // 3:1 against an empty day's cell in both themes (CHART_RAMP, lib/contrast.test.ts), so no day is lost
        visualMap: { min: 0, max: p95, orient: "horizontal", left: "center", bottom: 0, itemHeight: 120, itemWidth: 10,
          text: [`${fmtCompact(p95)} or more`, fmtCompact(0)], textGap: 8,
          formatter: (v: number) => fmtCompact(v), textStyle: { color: CHART_TEXT },
          inRange: { color: [...CHART_RAMP] } },
        calendar: {
          range: span.range,
          // the year label too: ECharts paints it a fixed light grey that vanishes on a light card
          cellSize: ["auto", 14], dayLabel: { color: CHART_TEXT }, yearLabel: { color: CHART_TEXT },
          monthLabel: { color: CHART_TEXT, fontSize: 10, nameMap: "en" },
          itemStyle: { color: CHART_SURFACE, borderColor: CHART_SPLIT },
          splitLine: { lineStyle: { color: CHART_AXIS } },
          // on a phone, on its side: the weekdays across the top, the months down the left (the title names them with
          // their years, so the year label goes), the scale under it
          ...(isMobile ? {
            orient: "vertical", cellSize: ["auto", CALENDAR_TAP], top: CALENDAR_PHONE.top, left: CALENDAR_PHONE.left,
            right: CALENDAR_PHONE.right, bottom: CALENDAR_PHONE.bottom, yearLabel: { show: false, color: CHART_TEXT },
            // the weekday names 8px over the first row (ECharts' default margin is half a cell, which put them over
            // the chart's top edge)
            dayLabel: { color: CHART_TEXT, nameMap: "en", firstDay: 0, margin: 8 },
          } : {}),
        },
        series: [{ type: "heatmap", coordinateSystem: "calendar",
          data: [...daily.entries()].map(([d, v]) => [d, v]) }],
      },
    };
  }, [filtered, shown.to, dataEnd, calendarMonths, isMobile]);
  // a phone's calendar is as tall as its weeks need, a CALENDAR_TAP row each, between its day labels and its scale
  const calendarPhoneHeight = calendar === null ? undefined
    : CALENDAR_PHONE.top + weeksSpanned(calendar.span.range) * CALENDAR_TAP + CALENDAR_PHONE.bottom;
  // the months the calendar spans, in its title: one month, or the first and the last, and whether the window holds
  // spending before them that it leaves out
  const calendarWords = calendar === null ? null
    : (calendar.span.from === calendar.span.to ? fmtMonth(calendar.span.from)
      : `${fmtMonth(calendar.span.from)} – ${fmtMonth(calendar.span.to)}`)
      + (calendar.span.capped ? "; earlier months not drawn" : "");

  // a card with nothing to chart keeps its frame and heading and says one line: no transaction at all (the page's long
  // line in its first card, the short one after it), or no spending in the selection (what to change)
  const noTransactions = data.transactions.length === 0;
  const nothing = (first = false) => (noTransactions ? <NoTransactions short={!first} /> : <EmptyState>{NO_SPENDING}</EmptyState>);

  return (
    <div className="space-y-4">
      <FilterBar filters={filters} update={update} clear={clear} filtered={selected}
        rowsPath={spendingPath(shown)} />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card testId="card-spend-by-category" title={`Monthly spend by category — ${click} a segment to zoom into that month + category`}>
          {monthly.months.length === 0 ? nothing(true) : <>
          <EChart option={stacked.option} tableData={stacked.table} height={330}
            onClick={(p) => {
              if (!p.seriesName) return;
              const [from, to] = monthBounds(String(p.name));
              // the "Other (N categories)" roll-up isn't a real category
              const isCat = !p.seriesName.startsWith("Other (");
              update({ from, to, ...(isCat ? { categories: [p.seriesName] } : {}) });
            }} />
          {partial.size > 0 && (
            <p className="mt-1 text-[10px] text-faint">
              * partial month — the data (or the From / To dates) cover only part of {partial.size === 1 ? "it" : "each"}
              {inProgress !== null && partial.has(inProgress) && `, and ${fmtMonth(inProgress)} is still in progress`}, so{" "}
              {partial.size === 1 ? "its bar is" : "their bars are"} lower than a whole month's
            </p>
          )}
          <SplitNote categories={monthly.categories} />
          </>}
        </Card>
        <Card testId="card-top-vendors" title={`Top vendors — ${click} for drill-down`}>
          {topVendors.length === 0 ? nothing() : (
          <EChart option={vendorChart.option} tableData={vendorChart.table} height={400}
            onClick={(p) => setDrillVendor(String(p.name))} />
          )}
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card testId="card-category-breakdown"
          title={breakdown === "treemap"
            ? `Category breakdown — ${click} a category to filter, ${click} again to zoom into its subcategories`
            : `Category breakdown — ${click} to filter`}
          right={<SegmentToggle label="Chart type" value={breakdown}
            options={[{ value: "treemap", label: "Treemap" }, { value: "donut", label: "Donut" }]}
            onChange={setBreakdown} />}>
          {breakdownOption === null ? nothing() : (
          <EChart option={breakdownOption} height={330}
            onClick={(p) => {
              // donut slices are categories; treemap leaves are subcategories,
              // which aren't a filter facet — scope to the parent category
              const cat = breakdown === "donut"
                ? String(p.name)
                : String(p.treePathInfo?.[1]?.name ?? p.name);
              if (cat && indexes.categoryList.includes(cat)) update({ categories: [cat] });
            }} />
          )}
        </Card>
        {/* the title names the two periods only while there is a comparison to show */}
        <Card testId="card-period-compare" title={`${compareMode === "mom" ? "Month-over-month" : "Year-over-year"}${
          compare !== null && compare.rows.length > 0 && compare.before === null ? `: ${compare.words}` : ""}`}
          right={<SegmentToggle label="Compare" value={compareMode}
            options={[{ value: "mom", label: "MoM" }, { value: "yoy", label: "YoY" }]}
            onChange={setCompareMode} />}>
          {compare === null || compare.rows.length === 0 ? nothing() : compare.before !== null ? (
            <NotEnoughHistory>the data begins {fmtDate(compare.before)}, after {fmtMonth(compare.prev)}, the month{" "}
              {fmtMonth(compare.cur)} would be set against.</NotEnoughHistory>
          ) : (
          <TableScroll label="Month-over-month table" stickyFirst>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-zinc-700 text-left text-zinc-500">
                <th className="py-1">Category</th>
                <th className="text-right">{fmtMonth(compare.cur)}</th>
                <th className="text-right">{fmtMonth(compare.prev)}</th>
                <th className="text-right">Δ</th>
              </tr>
            </thead>
            <tbody>
              {compare.rows.map((r) => {
                const d = r.current - r.previous;
                return (
                  <tr key={r.category} className="border-b border-zinc-800/60">
                    <td className="py-1.5">{r.category}</td>
                    <td className="text-right tabular-nums">{fmtUSD0(r.current)}</td>
                    <td className="text-right tabular-nums text-zinc-500">{fmtUSD0(r.previous)}</td>
                    <td className={`text-right tabular-nums ${d > 0 ? "text-red-400" : "text-emerald-400"}`}>
                      {fmtDelta(d)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </TableScroll>
          )}
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card testId="card-weekday-spend" title="Average daily spend by weekday (weekend in amber)">
          {hasWeekday ? <EChart option={weekdayOption} height={240} />
            : weekdayShort && hasOutflow ? (
              <NotEnoughHistory>
                the averages need {WEEKDAY_MIN_DAYS / 7} weeks of data in this window, so each weekday is seen four times;
                it holds {countOf(weekdayWindow?.days ?? 0, "day")}.
              </NotEnoughHistory>
            ) : nothing()}
        </Card>
        <Card testId="card-spend-calendar" title={`Daily spend calendar${calendarWords === null ? "" : ` (${calendarWords})`} — ${click} a day for its transactions`}>
          {calendar === null || !hasOutflow ? nothing() : (<>
          <EChart option={calendar.option} height={240} phoneHeight={calendarPhoneHeight}
            onClick={(p) => {
              // a day is a list of transactions: open it on the table, keeping the filters the
              // heatmap was drawn from
              const d = Array.isArray(p.value) ? String(p.value[0]) : null;
              if (d) navigate(spendingPath({ ...filters, from: d, to: d }));
            }} />
          {/* the scale starts at $0 in its first colour: a day with no spending is drawn blank, not in that colour */}
          <p data-testid="calendar-blank" className="mt-1 text-center text-[10px] text-faint">
            A blank day had no spending; the scale colours the days that had some.
          </p>
          </>)}
        </Card>
      </div>

      {drillVendor && (
        <VendorDrill vendor={drillVendor} onClose={() => setDrillVendor(null)}
          onFilter={() => { update({ vendors: [drillVendor] }); setDrillVendor(null); }} />
      )}
    </div>
  );
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useDeferredValue, useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import ChartDataTable, { CsvButton } from "../components/ChartDataTable";
import EChart from "../components/EChart";
import FilterBar from "../components/FilterBar";
import SaveNameBox from "../components/SaveNameBox";
import SplitNote from "../components/SplitNote";
import { Pick } from "../components/inputs";
import { Card, SegmentToggle, TAP } from "../components/ui";
import { useData } from "../data/DataContext";
import { useSavedReports } from "../data/useSavedReports";
import { applyFilters } from "../filters/filters";
import { useFilters } from "../filters/useFilters";
import { savedQuery, useSpendingDefaults } from "../filters/useSpendingDefaults";
import { monthInProgress } from "../lib/agg";
import { CARRIED_SEARCH } from "../lib/hubNav";
import { PREVIEW_ID, PREVIEW_STAMP } from "../lib/ids";
import { tryWithSaved } from "../lib/prefs";
import { groupColorOf, MAX_GROUPS, MEASURE_DEFINITIONS, periodLabel, reportInput, reportOption, reportTable, runReport } from "../lib/reportBuilder";
import {
  CHART_LABELS, CHARTS, describeReport, GROUP_BY_LABELS, GROUP_BYS, INTERVAL_LABELS, INTERVALS, MEASURE_LABELS, MEASURES,
  REPORT_KEYS, type ReportSpec, specFromSearch,
} from "../lib/reportSpec";
import { savedReportPath } from "../lib/savedFilters";

type ReportKey = (typeof REPORT_KEYS)[number];

/** Spending → Reports: group / interval / measure / chart over the FilterBar's rows (the same URL
 *  filter state as Explore and Transactions; the four report keys are this tab's own). The spec IS the
 *  URL, so a report is a shareable link and "saved" is just that link under a name (prefs.reports). */
export default function Reports() {
  const { data } = useData();
  const [filters, update, clear] = useFilters();
  useSpendingDefaults();
  const [sp, setSp] = useSearchParams();
  const spec = useMemo(() => specFromSearch(sp), [sp]);
  // every report-key write is a deliberate query: CARRIED_SEARCH keeps a Back onto it from reading as a
  // fresh visit (which would re-apply the six-month default window), exactly as useFilters' writers do. A write names
  // all four keys (the spec on screen with this one changed): a URL naming only some reads the rest from LINK_BASELINE
  const setSpecKey = (k: ReportKey, v: string) => setSp((prev) => {
    const next = new URLSearchParams(prev);
    const cur = specFromSearch(prev);
    for (const key of REPORT_KEYS) next.set(key, key === k ? v : cur[key]);
    return next;
  }, { replace: true, state: CARRIED_SEARCH });
  // the report follows the URL a beat behind the controls (a keystroke in the search box re-renders the bar at
  // once, the report once per pause); the pickers and the Save box read the live spec
  const shownFilters = useDeferredValue(filters);
  const shown = useDeferredValue(spec);
  // the report on screen is a beat behind the URL: a CSV taken now would export the previous spec
  const catchingUp = shown !== spec || shownFilters !== filters;
  const filtered = useMemo(() => applyFilters(data.transactions, shownFilters), [data.transactions, shownFilters]);
  const rows = useMemo(() => reportInput(data, shown), [data, shown]);
  // no period after the month in progress (lib/agg.ts monthInProgress), and the period holding it is starred
  const inProgress = useMemo(() => monthInProgress(data), [data]);
  const result = useMemo(() => runReport(rows, shown, MAX_GROUPS, inProgress), [rows, shown, inProgress]);
  const table = useMemo(() => ({
    ...reportTable(result, shown.measure, shown.interval),
    csvName: `report-${shown.measure}-by-${shown.groupBy}-per-${shown.interval}.csv`,
    label: describeReport(shown),
  }), [result, shown]);
  const option = useMemo(() => reportOption(result, shown,
    groupColorOf(shown, data.accounts, result.groups.filter((g) => g !== result.other))), [result, shown, data.accounts]);
  const n = result.periods.length;
  const title = `${describeReport(shown)} — ${n} period${n === 1 ? "" : "s"}`;
  return (
    <div className="space-y-4">
      <FilterBar filters={filters} update={update} clear={clear} filtered={filtered} compact canSaveView={false} />
      <Card testId="card-report-builder" title="Report builder">
        <div className="flex flex-wrap items-end gap-3 text-xs">
          <Pick label="Group by" value={spec.groupBy} options={GROUP_BYS} optionLabels={GROUP_BY_LABELS} onChange={(v) => setSpecKey("groupBy", v)} />
          <Pick label="Interval" value={spec.interval} options={INTERVALS} optionLabels={INTERVAL_LABELS} onChange={(v) => setSpecKey("interval", v)} />
          <Pick label="Measure" value={spec.measure} options={MEASURES} optionLabels={MEASURE_LABELS} onChange={(v) => setSpecKey("measure", v)} />
          <SegmentToggle label="Chart type" value={spec.chart} options={CHARTS.map((c) => ({ value: c, label: CHART_LABELS[c] }))} onChange={(v) => setSpecKey("chart", v)} />
          <SaveReport spec={spec} />
        </div>
        <SavedReportsMenu />
        <p className="mt-2 text-[11px] text-zinc-400">{MEASURE_DEFINITIONS[spec.measure]}</p>
        <p className="mt-1 text-[11px] leading-relaxed text-zinc-400">
          rows = the filters above, shared with Explore and Transactions. This bar hides type, amount, direction and
          transfers between your own accounts, but they still apply when carried in from Transactions (those transfers stay
          out unless turned on there) · groups past the top {MAX_GROUPS} by total roll into “Other (n)” · a period where the data has no transaction
          at all reads — (a gap in the feeds), one where it has transactions but none matched is a real 0 · * marks a period
          the From / To dates or the start or end of the data cover only in part, or that holds the month still in progress
          (a row dated into a later month waits for that month) · early periods can read low while accounts
          were still being added (Data → Caveats)
        </p>
      </Card>
      <Card testId="card-report-result" title={title}>
        {n === 0 ? <p className="text-xs text-zinc-500">No rows match these filters for this measure.</p>
          : shown.chart === "table" ? (
            <div className="text-[10px]">
              <CsvButton table={table} disabled={catchingUp} />
              <ChartDataTable table={table} caption={`Report: ${table.label}, by ${table.columns[0]}`} />
            </div>
          ) : <EChart option={option} height={360} tableData={table} csvDisabled={catchingUp} />}
        {n > 0 && result.partial.size > 0 && (
          <p className="mt-1 text-[10px] text-amber-300">
            partial period{result.partial.size === 1 ? "" : "s"} ({[...result.partial].map((p) => periodLabel(p, shown.interval, true, shown.chart !== "table")).join(", ")}): the
            From / To dates or the start or end of the data cover only part of {result.partial.size === 1 ? "it" : "each"}, or the
            month still in progress is in it, so{" "}
            {result.partial.size === 1 ? "its" : "their"} numbers are incomplete
          </p>
        )}
        {/* grouped by category the Groceries / Shopping bars include a vendor rule's split parts: same footnote as Explore,
            Budget, the income statement and the emergency-fund card (a subcategory group is "Category / Sub") */}
        {n > 0 && (shown.groupBy === "category" || shown.groupBy === "subcategory") && (
          <SplitNote categories={result.groups.filter((g) => g !== result.other).map((g) => g.split(" / ")[0])} />
        )}
      </Card>
    </div>
  );
}

/** "★ Save report": the current spec under a typed name, for the whole family (Data manages them). The same Save box as the
 *  FilterBar's "★ Save view" (components/SaveNameBox): it says why a save would be refused before the click, from the prefs ops'
 *  own verdict on an item with an id as long as a real one (PREVIEW_ID) and a constant stamp as long as a real one
 *  (PREVIEW_STAMP, so no clock is read per render): the 50-report cap, a spec too long to store, or a full prefs doc. */
function SaveReport({ spec }: { spec: ReportSpec }) {
  const { prefs, indexes } = useData();
  // a window that is a preset (the automatic six months a fresh visit wrote included) is saved as `win=`, so the report keeps
  // rolling with the data; dates the user picked are saved as the exact days - the same rule as the FilterBar's "★ Save view"
  // (filters/useSpendingDefaults savedFilterQuery)
  const saved: ReportSpec = { ...spec, filters: savedQuery(spec.filters, indexes.dataThrough) };
  const refusalFor = (name: string) => {
    const verdict = tryWithSaved(prefs.doc, "reports", { id: PREVIEW_ID, name: name.trim() || "x", spec: saved }, PREVIEW_STAMP);
    return typeof verdict === "string" ? verdict : null;
  };
  return (
    <div className="ml-auto flex flex-wrap items-center gap-1">
      <SaveNameBox kind="report" buttonLabel="★ Save report" nameLabel="Report name" placeholder="name this report"
        refusalFor={refusalFor} onSave={(name) => prefs.saveReport(name, saved)} />
    </div>
  );
}

/** The family's saved reports as a disclosure of links on this tab. Each opens the report exactly as saved:
 *  its filters and report keys, with the CARRIED_SEARCH marker, so a report saved without a date window opens
 *  all-time instead of getting the six-month default. A stale report (its category is gone) is not offered;
 *  the footer counts them and points at Data, where they are named. */
function SavedReportsMenu() {
  const { valid, stale } = useSavedReports();
  if (valid.length + stale.length === 0) return null;
  return (
    <details data-testid="saved-reports" className="mt-3 text-xs">
      <summary className={`inline-flex ${TAP} cursor-pointer select-none items-center text-blue-400 hover:underline`}>
        ★ Saved reports · {valid.length}
      </summary>
      <ul aria-label="Saved reports" className="mt-1 space-y-0.5">
        {valid.map((r) => (
          <li key={r.id} className="flex flex-wrap items-baseline gap-x-2">
            <Link to={savedReportPath(r)} state={CARRIED_SEARCH}
              className={`inline-flex ${TAP} items-center text-blue-400 hover:underline [overflow-wrap:anywhere]`}>{r.name}</Link>
            <span className="text-zinc-500">{describeReport(r.spec)}</span>
          </li>
        ))}
        {valid.length === 0 && <li className="text-zinc-500">no saved report fits this data</li>}
        {stale.length > 0 && (
          <li>
            <Link to="/data" className={`inline-flex ${TAP} items-center text-amber-300 hover:underline`}>
              {stale.length} stale report{stale.length === 1 ? "" : "s"} — see Data
            </Link>
          </li>
        )}
      </ul>
    </details>
  );
}

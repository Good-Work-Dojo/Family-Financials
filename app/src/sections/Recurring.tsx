// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import DateRangeBar from "../components/DateRangeBar";
import EChart from "../components/EChart";
import { EmptyState } from "../components/NoData";
import RequireData from "../components/RequireData";
import { Badge, Card, pillCls, StatCard, TableScroll } from "../components/ui";
import { useData } from "../data/DataContext";
import { NO_PRESET_REASON, type RangePreset } from "../filters/dateRange";
import { spendingLink, spendingPath } from "../filters/filters";
import { useDateRange } from "../filters/useDateRange";
import { monthInProgress } from "../lib/agg";
import { clock } from "../lib/clock";
import { baseGrid, baseLegend, CHART_AXIS, encodeHTML, moneyAxis, monthAxis } from "../lib/echarts";
import { fmtCompact, fmtDate, fmtMonth, fmtUSD, fmtUSD0 } from "../lib/format";
import {
  groupObligations, obligationMonths, obligationsByMonth, obligationsFrom, obligationsInMonths, upcomingObligations,
} from "../lib/obligations";
import {
  knownThroughFor, recurringKey, scheduleStatus, statusChip, visibleRecurring, type ScheduleStatus,
} from "../lib/schedules";
import { useVocab } from "../lib/useVocab";
import VendorDrill from "./VendorDrill";

const PRESETS: RangePreset[] = ["12m", "24m", "all"];

/** the tiles' few words with no recurring charge detected, and with none of them still active */
const NONE_DETECTED = "none detected yet";
const NONE_ACTIVE = "no active charge";

export default function Recurring() {
  return (
    <RequireData files={["recurring.json"]} what="Recurring & Subscriptions">
      <RecurringPage />
    </RequireData>
  );
}

function RecurringPage() {
  const { data, indexes } = useData();
  const vocab = useVocab();
  const navigate = useNavigate();
  const [drill, setDrill] = useState<string | null>(null);
  const [showInactive, setShowInactive] = useState(false);
  // window answers "what was I subscribed to then?" — an item is in-window
  // if its observed charge span overlaps [from, to]
  const dr = useDateRange("all");
  const [missedOnly, setMissedOnly] = useState(false);
  // clock-dependent on purpose (the ETL ships dates, the app judges them): the household's day, not the browser's
  const today = clock.today(data.assumptions.household.timezone);
  const status = useMemo(() => {
    const m = new Map<string, ScheduleStatus>();
    for (const r of data.recurring) {
      m.set(recurringKey(r), scheduleStatus(r, today, knownThroughFor(data.accounts, r.account_id)));
    }
    return m;
  }, [data.recurring, data.accounts, today]);

  // with an explicit historical window, "was active then" is the overlap test itself — a
  // now-lapsed gym membership should still show for last summer. The pill's count comes from the
  // same filter as the rows (lib/schedules.ts), so it always equals what pressing it shows.
  const { items, missedCount } = useMemo(() => visibleRecurring(data.recurring, {
    showInactive, explicit: dr.explicit, from: dr.range.from, to: dr.range.to, missedOnly, status,
  }), [data.recurring, showInactive, dr.range, dr.explicit, missedOnly, status]);

  const detected = data.recurring.length > 0;
  const active = data.recurring.filter((r) => r.active);
  // the export's own figures (etl/enrich/recurring.py annualized_cents): a monthly item's charge, a third of a
  // quarterly one and a twelfth of an annual one a month; the year is the export's yearly sums, never the monthly
  // figures (rounded to the cent) times twelve
  const monthlyTotal = active.reduce((s, r) => s + r.monthly_equivalent, 0);
  const annualTotal = active.reduce((s, r) => s + r.annualized, 0);
  const increases = data.recurring.reduce((s, r) => s + r.price_changes.filter((c) => c.to > c.from).length, 0);
  // null with no snapshot and no transaction: nothing to roll forward from or judge a flag at
  const dataEnd = obligationsFrom(data.manifest.current_snapshot_date, indexes.dataThrough);
  // age/renewal/overdue flags are judged at the window end, not "today",
  // so historical windows read correctly
  const effEnd = dr.range.to && (dataEnd === null || dr.range.to < dataEnd) ? dr.range.to : dataEnd;

  const flags = (r: (typeof items)[number]): { label: string; cls: string; title: string }[] => {
    const out = [] as { label: string; cls: string; title: string }[];
    if (r.price_changes.length) {
      const last = r.price_changes[r.price_changes.length - 1];
      if (last.to > last.from) out.push({
        label: `↑ ${fmtUSD(last.from)}→${fmtUSD(last.to)}`,
        cls: "bg-red-950 text-red-300",
        title: `price increase on ${fmtDate(last.date)}`,
      });
    }
    if (effEnd === null) return out;   // no day to judge age, renewal or lateness at
    const ageMonths = (new Date(effEnd).getTime() - new Date(r.first_seen).getTime()) / 2.6e9;
    if (r.active && ageMonths >= 12 && vocab.isSubscriptionLike(r.category)
      && r.monthly_equivalent < 60) {
      out.push({ label: "still using it?", cls: "bg-amber-950 text-amber-300",
        title: "a small subscription charged for a year or more — still using it? (a guess from its age and category)" });
    }
    if (r.active && r.cadence === "annual") {
      const days = (new Date(r.next_expected).getTime() - new Date(effEnd).getTime()) / 86_400_000;
      if (days >= 0 && days <= 45) out.push({
        label: `renews ~${fmtDate(r.next_expected)}`, cls: "bg-blue-950 text-blue-300",
        title: "annual renewal coming up — cancel window" });
    }
    // a promoted schedule's status chip replaces this heuristic
    if (!r.schedule && r.active && r.next_expected < effEnd) out.push({
      label: "overdue", cls: "bg-zinc-800 text-zinc-400",
      title: "expected charge hasn't appeared — may have been cancelled" });
    return out;
  };

  return (
    <div className="space-y-4">
      {/* the hub renders the section title; this row holds only the window control */}
      <div className="flex items-center justify-end gap-2">
        <DateRangeBar from={dr.range.from} to={dr.range.to} presets={PRESETS}
          activePreset={dr.active(PRESETS)} onPreset={dr.setPreset} onChange={dr.set}
          unavailable={dr.maxDate === null ? NO_PRESET_REASON : undefined}
          openEnds={{ first: indexes.dataRange?.first ?? null, last: indexes.dataRange?.last ?? null }} />
      </div>
      {/* nothing detected: no tile has a subject, so each reads "—" and says so (the obligations card below says what
          brings one); detected and none active: the count is a true 0 and the costs are no sum over nothing. The warning
          tone needs an increase to warn about */}
      <div data-testid="kpis-recurring" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {detected ? <StatCard label="Active recurring charges" value={String(active.length)} />
          : <StatCard label="Active recurring charges" value="—" sub={NONE_DETECTED} />}
        {active.length > 0 ? <>
          <StatCard label="Monthly cost" value={fmtUSD0(monthlyTotal)} />
          <StatCard label="Annualized" value={fmtUSD0(annualTotal)} />
        </> : <>
          <StatCard label="Monthly cost" value="—" sub={detected ? NONE_ACTIVE : NONE_DETECTED} />
          <StatCard label="Annualized" value="—" sub={detected ? NONE_ACTIVE : NONE_DETECTED} />
        </>}
        {detected ? <StatCard label="Price increases seen" tone={increases > 0 ? "warn" : undefined} value={String(increases)} />
          : <StatCard label="Price increases seen" value="—" sub={NONE_DETECTED} />}
      </div>

      <ObligationsCard dataEnd={dataEnd} />

      <Card testId="card-recurring-table" title="Detected recurring charges (same vendor, regular cadence, similar amount)"
        right={
          <div className="flex items-center gap-3">
            {dr.explicit && (
              <span className="text-[10px] text-amber-400"
                title="with a date window set, anything whose charges overlap the window is shown — including since-cancelled items">
                window applied · lapsed included
              </span>
            )}
            {/* the button is the 44px phone tap target; the pill look lives on the inner span */}
            <button type="button" onClick={() => setMissedOnly(!missedOnly)} aria-pressed={missedOnly}
              className="flex min-h-11 items-center md:min-h-0">
              <span className={pillCls(missedOnly, "border-red-800 bg-red-950/60 font-medium text-red-200",
                "border-zinc-700 text-zinc-400 hover:bg-zinc-900")}>
                missed only <span className="text-zinc-500">{missedCount}</span>
              </span>
            </button>
            <label className="flex min-h-11 items-center gap-1 text-xs text-zinc-400 md:min-h-0">
              <input type="checkbox" checked={showInactive}
                onChange={(e) => setShowInactive(e.target.checked)} className="accent-blue-500" />
              show lapsed
            </label>
          </div>
        }>
        {/* phones get a stacked list (nine columns do not fit 390px); jsdom applies no media
            query, so tests see both this list and the table below */}
        <ul className="divide-y divide-zinc-800/60 md:hidden">
          {items.length === 0 && (
            <li className="py-3 text-center text-xs text-zinc-500">{missedOnly ? "no missed schedules" : "nothing to show"}</li>
          )}
          {items.map((r) => {
            const chip = statusChip(status.get(recurringKey(r)) ?? "inactive", r);
            return (
              <li key={recurringKey(r)} className={`py-1 text-xs ${r.active ? "" : "opacity-50"}`}>
                <div className="flex items-center justify-between gap-2">
                  {/* 44px tap targets (this list is phone-only): the vendor button and the txns link */}
                  <button className="flex min-h-11 min-w-0 items-center text-left font-medium hover:text-blue-300"
                    onClick={() => setDrill(r.vendor)}>
                    <span className="min-w-0 break-words">{r.vendor}</span>
                  </button>
                  <span className="shrink-0 font-semibold tabular-nums">
                    {fmtUSD(r.monthly_equivalent)}<span className="font-normal text-zinc-500">/mo</span>
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  {chip && <Badge title={chip.title} cls={chip.cls}>{chip.label}</Badge>}
                  {flags(r).map((f) => <Badge key={f.label} title={f.title} cls={f.cls}>{f.label}</Badge>)}
                </div>
                <div className="flex flex-wrap items-center gap-x-3 text-[11px] text-zinc-500">
                  <span>{r.category}</span>
                  <span>{r.cadence}{r.amount_mode === "variable" ? " ~" : ""} · {fmtUSD(r.typical_amount)}</span>
                  <span>last {fmtDate(r.last_seen)}</span>
                  <a href={spendingLink({ vendors: [r.vendor] })}
                    className="inline-flex min-h-11 min-w-11 items-center text-blue-400 hover:underline">txns →</a>
                </div>
              </li>
            );
          })}
        </ul>
        {/* the price sparkline is desktop-only: a 26px chart per row adds nothing a phone can read,
            and the "↑ $a→$b" flag above already carries the increase */}
        <div className="hidden md:block">
        <TableScroll label="Detected recurring charges table">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-zinc-700 text-left text-zinc-500">
              <th className="py-1">Vendor</th><th>Cadence</th>
              <th className="pl-3 text-right">Typical</th>
              <th className="pl-3 text-right">Monthly eq.</th>
              <th className="pl-3">Last charged</th><th>Status</th><th>History</th><th>Flags</th><th></th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && (
              <tr><td colSpan={9} className="py-3 text-center text-zinc-500">
                {missedOnly ? "no missed schedules" : "nothing to show"}
              </td></tr>
            )}
            {items.map((r) => {
              const chip = statusChip(status.get(recurringKey(r)) ?? "inactive", r);
              return (
                <tr key={recurringKey(r)}
                  className={`border-b border-zinc-800/60 ${r.active ? "" : "opacity-50"}`}>
                  <td className="py-1.5">
                    <button className="font-medium hover:text-blue-300" onClick={() => setDrill(r.vendor)}>
                      {r.vendor}
                    </button>
                    <span className="ml-1 text-faint">{r.category}</span>
                  </td>
                  <td>{r.cadence}{r.amount_mode === "variable" ? " ~" : ""}</td>
                  <td className="pl-3 text-right tabular-nums">{fmtUSD(r.typical_amount)}</td>
                  <td className="pl-3 text-right font-semibold tabular-nums">{fmtUSD(r.monthly_equivalent)}</td>
                  <td className="pl-3 text-zinc-400">{fmtDate(r.last_seen)}</td>
                  <td>{chip
                    ? <Badge title={chip.title} cls={chip.cls}>{chip.label}</Badge>
                    : <span className="text-zinc-700" title={r.schedule ? "no charge expected soon" : "not regular enough to be a schedule yet"}>—</span>}</td>
                  <td className="w-24"><PriceSpark item={r} /></td>
                  <td>
                    <div className="flex flex-wrap gap-1">
                      {flags(r).map((f) => (
                        <Badge key={f.label} title={f.title} cls={f.cls}>{f.label}</Badge>
                      ))}
                    </div>
                  </td>
                  <td>
                    <a href={spendingLink({ vendors: [r.vendor] })}
                      className="text-blue-400 hover:underline">txns →</a>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </TableScroll>
        </div>
        <p className="mt-1 text-[10px] text-faint">
          Status judges each detected schedule against today and the account's own data:
          "data stale" means the expected date has passed but that account's transactions do not
          reach it yet — not a missed payment. A charge becomes a schedule by itself once it repeats at a regular
          interval: four times or more for a weekly or every-two-weeks charge, three for a longer one, with at least
          seven in ten of the gaps between charges fitting that interval (more when the amount varies).
        </p>
      </Card>
      {/* Recurring has no FilterBar to filter in place: "filter to vendor" opens all of that vendor's
          rows on the Transactions tab (a deep link with a query, so no default window is applied) */}
      {drill && <VendorDrill vendor={drill} onClose={() => setDrill(null)}
        onFilter={() => { const v = drill; setDrill(null); navigate(spendingPath({ vendors: [v] })); }} />}
    </div>
  );
}

const OBLIGATIONS_TITLE = "Upcoming obligations — known bills, next 12 months";
/** the card's line when no bill is expected: nothing detected yet (the detector's rule, etl/enrich/recurring.py: three
 *  charges or more from one vendor on one account, at a regular weekly-to-yearly interval, for a similar amount), or
 *  every detected charge has lapsed */
const NO_BILLS = "No bills detected yet. A charge is recognised once one vendor has charged the same account at least "
  + "three times at a regular interval, weekly to yearly, for a similar amount.";
const NO_ACTIVE_BILLS = "No detected recurring charge is still active, so no bill is expected in the next 12 months.";

/** known bills over the twelve months after the month in progress — the lumpy ones are the surprises; with none the
 *  card says why */
function ObligationsCard({ dataEnd }: { dataEnd: string | null }) {
  const { data } = useData();
  // twelve bars, the first the month after the month in progress (never a month already under way), each the charges
  // expected in it; one projection feeds the chart, its tooltip and the "all renewals" list below it, so they cannot
  // disagree, over the window Planning's lumpy-bills KPI totals
  const inProgress = useMemo(() => monthInProgress(data), [data]);
  const months = useMemo(() => obligationMonths(inProgress), [inProgress]);
  const obs = useMemo(() => obligationsInMonths(data.recurring, months), [data.recurring, months]);
  const byMonth = useMemo(() => obligationsByMonth(obs, months), [obs, months]);
  const bigBills = useMemo(() => obs.filter((o) => o.lumpy), [obs]);
  // the next 90 days, one entry per vendor ("× 2" when a quarterly bill falls twice), not one per occurrence
  const next90 = useMemo(() => (dataEnd === null ? [] : groupObligations(upcomingObligations(data.recurring, dataEnd, 3)
    .filter((o) => o.lumpy)).slice(0, 8)), [data.recurring, dataEnd]);
  if (obs.length === 0 || inProgress === null) {
    return (
      <Card testId="card-obligations" title={OBLIGATIONS_TITLE}>
        <EmptyState>{data.recurring.length > 0 && !data.recurring.some((r) => r.active) ? NO_ACTIVE_BILLS : NO_BILLS}</EmptyState>
      </Card>
    );
  }
  const option = {
    grid: baseGrid,
    legend: baseLegend(),
    tooltip: {
      trigger: "axis",
      formatter: (ps: any[]) => {
        const m = byMonth[ps[0].dataIndex];
        // the vendor is bank-descriptor text the ETL derived, and ECharts sets this string as innerHTML: escape it
        const lumps = m.lumpy.slice(0, 6).map((o) =>
          `<div style="color:#a1a1aa">· ${encodeHTML(o.vendor)} ${fmtUSD0(o.amount)} (${o.cadence})</div>`).join("");
        return `<b>${fmtMonth(m.month)}</b><div>known bills: ${fmtUSD0(m.total)}</div>${lumps}`;
      },
    },
    xAxis: monthAxis(byMonth.map((m) => m.month)),
    yAxis: moneyAxis(fmtCompact),
    series: [
      { name: "Routine", type: "bar", stack: "ob", color: CHART_AXIS,
        data: byMonth.map((m) => Math.round(m.total - m.lumpyTotal)) },
      { name: "Renewals (annual/quarterly)", type: "bar", stack: "ob", color: "#f59e0b",
        data: byMonth.map((m) => Math.round(m.lumpyTotal)) },
    ],
  };
  return (
    <Card testId="card-obligations" title={OBLIGATIONS_TITLE}>
      <EChart option={option} height={220} />
      {next90.length > 0 && (
        <div className="mt-2 text-xs">
          <span className="font-medium text-zinc-300">Renewals in the next 90 days: </span>
          {next90.map((g) => (
            <span key={g.vendor + g.amount} className="mr-3 text-zinc-400">
              {g.vendor}{g.count > 1 && <span className="text-zinc-500"> × {g.count}</span>}{" "}
              <span className="tabular-nums">{fmtUSD0(g.amount)}</span>
              <span className="text-faint"> ~{fmtDate(g.date)}</span>
            </span>
          ))}
        </div>
      )}
      {/* months 4-12 of the Life report's "Big bills ahead" list used to live only in a hover tooltip (no dates, six a month); this is
          that list: every renewal over the next 12 months, by name, cadence, amount and date */}
      {bigBills.length > 0 && (
        <details data-testid="obligations-all-bills" className="mt-2 text-xs">
          <summary className="min-h-[40px] cursor-pointer select-none py-2 text-zinc-400 max-md:min-h-11">
            All renewals, next 12 months — {bigBills.length} charge{bigBills.length === 1 ? "" : "s"}
            {" · "}{fmtUSD0(bigBills.reduce((s, o) => s + o.amount, 0))}
          </summary>
          <ul className="space-y-1">
            {bigBills.map((o) => (
              <li key={`${o.vendor}|${o.date}`} className="flex flex-wrap items-baseline justify-between gap-x-3 text-zinc-300">
                <span className="min-w-0">{o.vendor} <span className="text-faint">· {o.cadence}</span></span>
                <span className="shrink-0 tabular-nums">{fmtUSD0(o.amount)} <span className="text-faint">~{fmtDate(o.date)}</span></span>
              </li>
            ))}
          </ul>
        </details>
      )}
      <p className="mt-1 text-[10px] text-faint">
        The twelve months after {fmtMonth(inProgress)}, which is still in progress. Projected from detected recurring
        charges and their cadence — one-off spending isn't included. Amber = annual and quarterly renewals; weekly,
        biweekly and monthly bills stay grey however large.
      </p>
    </Card>
  );
}

function PriceSpark({ item }: { item: { typical_amount: number; price_changes: { date: string; from: number; to: number }[] } }) {
  if (!item.price_changes.length) return <span className="text-zinc-700">flat</span>;
  const points = [item.price_changes[0].from, ...item.price_changes.map((c) => c.to)];
  const option = {
    grid: { left: 0, right: 0, top: 4, bottom: 0 },
    xAxis: { type: "category", show: false, data: points.map((_, i) => i) },
    yAxis: { type: "value", show: false, min: Math.min(...points) * 0.9 },
    series: [{ type: "line", data: points, symbolSize: 3,
      color: "#f59e0b", lineStyle: { width: 1.5 } }],
  };
  return <EChart option={option} height={26} />;
}

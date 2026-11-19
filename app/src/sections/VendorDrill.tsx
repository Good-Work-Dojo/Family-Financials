// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo, useRef } from "react";
import EChart from "../components/EChart";
import { useData } from "../data/DataContext";
import { countedRows, isSpend, monthInProgress, monthsBetween } from "../lib/agg";
import { baseGrid, moneyAxis, moneyTooltip, monthAxis } from "../lib/echarts";
import { countOf, fmtCompact, fmtDate, fmtDelta, fmtUSD, fmtUSD0 } from "../lib/format";
import { slope } from "../lib/stats";
import { useFocusTrap } from "../lib/useFocusTrap";

/** Side panel: per-vendor history, frequency, average ticket, trend. */
export default function VendorDrill({ vendor, onClose, onFilter }: {
  vendor: string; onClose: () => void; onFilter: () => void;
}) {
  const { data, indexes } = useData();
  const panelRef = useRef<HTMLDivElement>(null);
  useFocusTrap(panelRef, onClose);
  // the vendor's spending up to the month in progress (lib/agg.ts countedRows): a charge the bank dated into a month that
  // has not begun (an autopay) is in no bar, total, average, cadence or "last seen" until that month begins; the
  // register lists it, and the panel says how many it leaves out
  const inProgress = useMemo(() => monthInProgress(data), [data]);
  const { rows, ahead } = useMemo(() => {
    const spend = (indexes.byVendor.get(vendor) ?? []).filter((t) => isSpend(t) && t.amount < 0);
    const counted = countedRows(spend, inProgress).sort((a, b) => a.date.localeCompare(b.date));
    return { rows: counted, ahead: spend.length - counted.length };
  }, [indexes.byVendor, vendor, inProgress]);

  const stats = useMemo(() => {
    if (!rows.length) return null;
    const total = rows.reduce((s, t) => s - t.amount, 0);
    const days = (new Date(rows[rows.length - 1].date).getTime()
      - new Date(rows[0].date).getTime()) / 86_400_000;
    const monthly = new Map<string, number>();
    for (const t of rows) {
      const m = t.date.slice(0, 7);
      monthly.set(m, (monthly.get(m) ?? 0) - t.amount);
    }
    const months = monthsBetween(rows[0].date, rows[rows.length - 1].date);
    const series = months.map((m) => monthly.get(m) ?? 0);
    const trendPerMonth = slope(series);
    return {
      total, count: rows.length, avg: total / rows.length,
      cadenceDays: rows.length > 1 ? days / (rows.length - 1) : null,
      months, series, trendPerMonth,
      first: rows[0].date, last: rows[rows.length - 1].date,
    };
  }, [rows]);

  // above the figures, so it stands whether or not any charge is counted yet (a vendor whose one charge is dated ahead)
  const aheadNote = ahead > 0 && (
    <p data-testid="vendor-drill-ahead" className="mb-2 text-[11px] text-faint">
      {countOf(ahead, "charge")} dated ahead, into a month that has not begun: counted from that month on (the
      Transactions tab lists {ahead === 1 ? "it" : "them"})
    </p>
  );

  const chartOption = useMemo(() => stats && ({
    grid: { ...baseGrid, top: 16 },
    tooltip: moneyTooltip(),
    xAxis: monthAxis(stats.months),
    yAxis: moneyAxis(fmtCompact),
    series: [{ type: "bar", color: "#3b82f6", data: stats.series }],
  }), [stats]);

  return (
    <div data-testid="card-vendor-drill" className="fixed inset-0 z-40 flex justify-end bg-black/50" onClick={onClose}>
      <div ref={panelRef} role="dialog" aria-modal="true" aria-labelledby="vendor-drill-title" tabIndex={-1}
        className="h-full w-full max-w-lg overflow-y-auto border-l border-zinc-700 bg-zinc-950 p-4 outline-none sm:p-5"
        onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 id="vendor-drill-title" className="min-w-0 break-words text-lg font-bold">{vendor}</h2>
          <div className="flex gap-2">
            <button onClick={onFilter}
              className="min-h-[40px] rounded-md border border-blue-700 px-2 py-1 text-xs text-blue-300 sm:min-h-0">
              filter to vendor
            </button>
            <button onClick={onClose} aria-label="Close"
              className="flex min-h-[40px] min-w-[40px] items-center justify-center text-zinc-500 hover:text-zinc-200 sm:min-h-0 sm:min-w-0 sm:px-2">✕</button>
          </div>
        </div>
        {aheadNote}
        {!stats ? <p className="text-sm text-zinc-500">{ahead > 0 ? "No spending counted yet." : "No spending found."}</p> : (
          <>
            <div className="grid grid-cols-2 gap-2 text-sm">
              <Stat l="Total spent" v={fmtUSD0(stats.total)} />
              <Stat l="Transactions" v={String(stats.count)} />
              <Stat l="Average ticket" v={fmtUSD(stats.avg)} />
              <Stat l="Avg days between" v={stats.cadenceDays ? stats.cadenceDays.toFixed(0) : "—"} />
              <Stat l="First seen" v={fmtDate(stats.first)} />
              <Stat l="Last seen" v={fmtDate(stats.last)} />
              <Stat l="Monthly trend" v={`${fmtDelta(stats.trendPerMonth)}/mo`} />
            </div>
            <div className="mt-4">
              {chartOption && <EChart option={chartOption} height={200} />}
            </div>
            <h3 className="mb-1 mt-4 text-xs font-semibold text-zinc-400">Recent transactions</h3>
            <ul className="space-y-1 text-xs">
              {[...rows].reverse().slice(0, 15).map((t) => (
                <li key={t.id} className="flex justify-between border-b border-zinc-800/60 py-1">
                  <span className="text-zinc-400">{fmtDate(t.date)} · {t.account_name}</span>
                  <span className="tabular-nums text-red-300">{fmtUSD(t.amount)}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}

function Stat({ l, v }: { l: string; v: string }) {
  return (
    <div className="rounded-lg bg-zinc-900 p-2">
      <div className="text-[10px] uppercase text-zinc-500">{l}</div>
      <div className="font-semibold tabular-nums">{v}</div>
    </div>
  );
}
